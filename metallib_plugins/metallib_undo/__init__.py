# MetalLib undo -- every save can be taken back (Tools -> "Undo a save...").
#
# Before Picard writes a file, its current tags are recorded in an undo journal (see
# undo_store.py). If that snapshot cannot be taken, the file is NOT saved: it gets an error
# instead -- a save without a way back never happens. The journal also records where the file
# ended up (rename/move), so undo puts the old tags back and moves the file home again.
# Undo never deletes anything.
#
# Picard 3.0 has no plugin hook that can veto a save, so this wraps File._save_and_rename (the
# save itself, in Picard's save thread -- so snapshots never freeze the window, audit part 2 M6)
# and File._saving_finished; disable() restores both.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial
import os
import threading
import time

from PyQt6 import QtWidgets

from picard.file import File
from picard.plugin3.api import (
    BaseAction,
    PluginApi,
)
from picard.util import thread

from .undo_store import UndoJournal


_ATTR = '_metallib_undo_entry'
_api = None
_journal = None
_journal_lock = threading.Lock()
_originals = {}


def journal():
    global _journal
    with _journal_lock:                 # first used from the save thread(s)
        if _journal is None:
            from picard.const.appdirs import plugin_folder
            _journal = UndoJournal(os.path.join(os.path.dirname(os.path.abspath(plugin_folder())), 'metallib',
                                                'undo'))
            try:
                _journal.prune()        # old saves go (audit part 2 M6: the journal only ever grew)
            except Exception:
                _api.logger.exception("undo journal: pruning failed")
        return _journal


def _save_and_rename(self, old_filename, metadata):
    # Runs in Picard's save thread. Picard itself skips removed files and saves while quitting.
    if self.state != File.State.REMOVED and not self.tagger.stopping:
        try:
            setattr(self, _ATTR, journal().record(old_filename))
        except Exception as e:
            # No snapshot, no save: the user's rule is that every write must be reversible. The
            # error ends up on the file (Picard's _saving_finished), which is then not written.
            _api.logger.error("not saving %r: undo snapshot failed: %s", old_filename, e)
            raise OSError('MetalLib: not saved, because its current tags could not be backed up '
                          'for undo (%s)' % e) from e
    return _originals['save_and_rename'](self, old_filename, metadata)


def _saving_finished(self, result=None, error=None):
    entry = getattr(self, _ATTR, None)
    try:
        return _originals['saving_finished'](self, result=result, error=error)
    finally:
        if entry is not None:
            # Recorded even when the save failed half-way: a partly written file is exactly what
            # someone would want to undo. self.filename is the new path after a rename.
            try:
                journal().saved(entry, self.filename)
            except Exception:
                _api.logger.exception("undo journal: could not record save of %r", self.filename)
            setattr(self, _ATTR, None)


def _when(ts):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))


class UndoSave(BaseAction):
    TITLE = "Undo a save..."

    def callback(self, objs):
        batches = journal().batches()
        if not batches:
            QtWidgets.QMessageBox.information(self.tagger.window, 'MetalLib undo', 'There is no save to undo.')
            return
        rows = []
        for batch, at, entries in batches:
            folders = sorted({os.path.dirname(e['new_path']) for e in entries})
            rows.append((_when(at), str(len(entries)), folders[0] + (' (+%d more)' % (len(folders) - 1)
                                                                    if len(folders) > 1 else '')))
        i = _pick(self.tagger.window, 'Pick a save to undo (newest first)', ('Saved at', 'Files', 'Folder'), rows)
        if i is None:
            return
        batch, at, entries = batches[i]
        ok = QtWidgets.QMessageBox.question(
            self.tagger.window, 'MetalLib undo',
            'Put back the tags of %d file(s) as they were before the save of %s,\n'
            'and move renamed files back to their old folders?\n\nNothing is deleted.' % (len(entries), _when(at)))
        if ok != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        # The files are about to change on disk under Picard: take them out of Picard first, and
        # load them again from their restored paths afterwards.
        paths = {os.path.normcase(e['new_path']) for e in entries} | {os.path.normcase(e['old_path']) for e in entries}
        stale = [f for f in list(self.tagger.files.values()) if os.path.normcase(f.filename) in paths]
        if stale:
            self.tagger.remove_files(stale)
        self.tagger.window.set_statusbar_message('MetalLib: undoing the save of %s...', _when(at))
        thread.run_task(partial(journal().undo_batch, batch), partial(_undo_done, self.tagger, entries))


def _undo_done(tagger, entries, result=None, error=None):
    if error:
        QtWidgets.QMessageBox.critical(tagger.window, 'MetalLib undo', 'Undo failed: %s' % error)
        return
    failed = [msg for _, ok, msg in result if not ok]
    restored = [e['old_path'] for e in entries if os.path.exists(e['old_path'])]
    restored += [e['new_path'] for e in entries
                 if not os.path.exists(e['old_path']) and os.path.exists(e['new_path'])]
    if restored:
        tagger.add_files(sorted(set(restored)))
    text = 'Undid %d of %d file(s).' % (len(result) - len(failed), len(result))
    if failed:
        text += '\n\nNot fully undone:\n' + '\n'.join(failed[:20])
    QtWidgets.QMessageBox.information(tagger.window, 'MetalLib undo', text)


def _pick(parent, title, headers, rows):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle('MetalLib — ' + title)
    layout = QtWidgets.QVBoxLayout(dialog)
    table = QtWidgets.QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
    table.resizeColumnsToContents()
    table.selectRow(0)
    table.doubleClicked.connect(dialog.accept)
    layout.addWidget(table)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                         | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.resize(900, 360)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or not table.selectedItems():
        return None
    return table.currentRow()


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    _originals['save_and_rename'] = File._save_and_rename
    _originals['saving_finished'] = File._saving_finished
    File._save_and_rename = _save_and_rename
    File._saving_finished = _saving_finished
    api.register_tools_menu_action(UndoSave)


def disable() -> None:
    if 'save_and_rename' in _originals:
        File._save_and_rename = _originals['save_and_rename']
    if 'saving_finished' in _originals:
        File._saving_finished = _originals['saving_finished']

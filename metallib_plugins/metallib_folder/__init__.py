# MetalLib folder contents -- the files Picard does not show.
#
#   $folder()                      for a custom column: album / cluster rows show "clean" or
#                                  "2 junk, 1 check, 1 rename" for their folder(s)
#   right-click album / cluster -> "Folder contents..."   every non-album file with a verdict;
#                                  tick and "Move to trash" / apply the cover rename; "Undo last clean-up"
#   the Extra files panel          a row above the tag panel: tick / rename / preview the album's
#                                  extra files; they move (or go to the trash) when it is saved --
#                                  see extras_panel.py
#
# Junk goes to .metallib_trash at the root of the same drive/share -- never deleted, always undoable.
# See folder_scan.py for the rules.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import time

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.plugin3.api import (
    Album,
    BaseAction,
    Cluster,
    PluginApi,
)

from .folder_scan import (
    CHECK,
    JUNK,
    KEEP,
    RENAME,
    ActionLog,
    move_to_trash,
    new_batch,
    rename,
    scan,
    summary,
)


_api = None
_cache = {}            # frozenset(folders) -> (time, summary text)
CACHE_S = 60


def _log():
    from picard.const.appdirs import plugin_folder
    return ActionLog(os.path.join(os.path.dirname(os.path.abspath(plugin_folder())), 'metallib', 'folder_actions.jsonl'))


def _files(item):
    if isinstance(item, Album):
        return list(item.iterfiles())
    if isinstance(item, Cluster) and not item.special:
        return list(item.iterfiles())
    return []


def _names(item):
    md = item.metadata
    return md['albumartist'] or md['artist'], md['album']


def _scan_item(item):
    files = _files(item)
    folders = {os.path.dirname(f.filename) for f in files}
    # A disc folder ("CD1") belongs to its album folder: scan the parent too.
    folders |= {os.path.dirname(d) for d in folders if len(os.path.basename(d)) <= 12
                and os.path.basename(d).lower().replace(' ', '').startswith(('cd', 'disc', 'disk'))}
    artist, album = _names(item)
    return scan(folders, [f.filename for f in files], artist, album), folders


def _owner(metadata):
    tagger = _api.tagger
    for album in tagger.albums.values():
        if album.metadata is metadata:
            return album
    for cluster in tagger.clusters:
        if cluster.metadata is metadata:
            return cluster
    return None


def folder_summary(parser):
    item = _owner(parser.context) if parser.file is None else None
    if item is None:
        return ''
    files = _files(item)
    key = frozenset(os.path.dirname(f.filename) for f in files)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_S:
        return cached[1]
    text = summary(_scan_item(item)[0]) if files else ''
    _cache[key] = (time.time(), text)
    return text


class FolderContents(BaseAction):
    TITLE = "Folder contents..."

    def callback(self, objs):
        for obj in objs:
            if _files(obj):
                FolderDialog(self.tagger.window, obj).exec()


class FolderDialog(QtWidgets.QDialog):
    COLS = ('', 'File', 'Size', 'Verdict', 'Why')

    def __init__(self, parent, item):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle('MetalLib — folder contents: %s' % (item.metadata['album'] or ''))
        layout = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.table = QtWidgets.QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QtWidgets.QHBoxLayout()
        self.trash_btn = QtWidgets.QPushButton('Move ticked to trash')
        self.rename_btn = QtWidgets.QPushButton('Apply ticked renames')
        self.undo_btn = QtWidgets.QPushButton('Undo last clean-up')
        close = QtWidgets.QPushButton('Close')
        for b in (self.trash_btn, self.rename_btn, self.undo_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.trash_btn.clicked.connect(self._trash)
        self.rename_btn.clicked.connect(self._rename)
        self.undo_btn.clicked.connect(self._undo)
        close.clicked.connect(self.accept)
        self.resize(1000, 420)
        self._reload()

    def _reload(self):
        self.entries, self.folders = _scan_item(self.item)
        _cache.clear()
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            box = QtWidgets.QTableWidgetItem()
            if e['verdict'] in (JUNK, CHECK, RENAME):
                box.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                # Pre-tick only what the rules are sure about; "check" is the user's call.
                box.setCheckState(QtCore.Qt.CheckState.Checked if e['verdict'] in (JUNK, RENAME)
                                  else QtCore.Qt.CheckState.Unchecked)
            else:
                box.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, 0, box)
            size = '?' if e['size'] is None else ('%d B' % e['size'] if e['size'] < 10240 else '%.1f MB' % (e['size'] / 1048576))
            why = e['reason'] + (' -> %s' % os.path.basename(e['target']) if e['target'] else '')
            for c, text in enumerate((e['name'], size, e['verdict'], why), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        n = summary(self.entries)
        self.info.setText('Folder(s): %s<br><b>%s</b>. Junk is moved to <i>.metallib_trash</i> at the root of its drive '
                          '(never deleted) and every action can be undone here.'
                          % ('<br>'.join(sorted(self.folders)), 'Nothing to clean up' if n == 'clean' else n))
        self.undo_btn.setEnabled(_log().last_batch() is not None)

    def _ticked(self, verdicts):
        return [e for r, e in enumerate(self.entries)
                if e['verdict'] in verdicts and self.table.item(r, 0).checkState() == QtCore.Qt.CheckState.Checked]

    def _trash(self):
        chosen = self._ticked((JUNK, CHECK))
        if not chosen:
            return
        if QtWidgets.QMessageBox.question(self, 'MetalLib', 'Move %d file(s) to .metallib_trash? You can undo this.'
                                          % len(chosen)) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._run(lambda e, log, batch: move_to_trash(e['path'], log, batch), chosen)

    def _rename(self):
        self._run(lambda e, log, batch: rename(e['path'], e['target'], log, batch), self._ticked((RENAME,)))

    def _run(self, action, entries):
        log, batch, errors = _log(), new_batch(), []
        for e in entries:
            try:
                action(e, log, batch)
            except OSError as err:
                errors.append('%s: %s' % (e['name'], err))
        if errors:
            QtWidgets.QMessageBox.warning(self, 'MetalLib', 'Some files were left alone:\n' + '\n'.join(errors[:20]))
        self._reload()

    def _undo(self):
        log = _log()
        batch = log.last_batch()
        if batch is None:
            return
        bad = [msg for ok, msg in log.undo(batch) if not ok]
        if bad:
            QtWidgets.QMessageBox.warning(self, 'MetalLib', 'Not restored:\n' + '\n'.join(bad[:20]))
        self._reload()


def disable() -> None:
    from . import extras_panel
    extras_panel.uninstall()


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    from . import extras_panel
    api.plugin_config.register_option(extras_panel.LAYOUT_OPTION, '')
    api.plugin_config.register_option(extras_panel.PREVIEW_OPTION, '')
    api.plugin_config.register_option(extras_panel.COLUMNS_OPTION, '')
    extras_panel.install(api, _log)
    api.register_file_pre_save_processor(extras_panel.on_file_saving)
    api.register_file_post_save_processor(extras_panel.on_file_saved)
    api.register_album_action(FolderContents)
    api.register_cluster_action(FolderContents)
    api.register_script_function(folder_summary, name='folder', documentation=(
        '`$folder()`\n\nAlbum / cluster rows: what else is in the folder -- "clean" or e.g. "2 junk, 1 rename".'))


__all__ = ['disable', 'enable', 'KEEP']

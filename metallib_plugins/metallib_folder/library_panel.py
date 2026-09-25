# MetalLib -- the "Already in your library" list: right half of the Extra files row (user, 2026-09-25).
#
# For the selected album: the band's albums already in the library roots (Picard's "move files to"
# folder = staging, e.g. Z:\Metal, and the subfolders of its archive sibling, e.g. Z:\1 Metal\1 Sorted
# and 3 Greek), each compared with the folder the album will move into (library.py). Most urgent
# first: red = saving would merge into that exact folder, orange = the same album/pressing/quality
# already there, amber = other quality, blue = other pressing, grey = the band's other albums.
# Scanned in the background (network shares); rescanned when the target folder changes (a pressing
# pick, a tag edit). Double-click opens the folder.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial
import json
import os

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.util import thread

from .library import (
    COLLISION,
    OTHER,
    PRESSING,
    QUALITY,
    REDUNDANT,
    THIS,
    default_roots,
    scan,
)


COLUMNS_OPTION = 'library_columns'
COLS = ('Where', 'Album folder', 'Tracks', 'Images', 'What')
COLOURS = {COLLISION: '#d32f2f', REDUNDANT: '#e65100', QUALITY: '#a07800', PRESSING: '#1e62c9'}
POLL_MS = 1500


class LibraryPanel(QtWidgets.QWidget):
    def __init__(self, api, destination, sources, parent=None):
        super().__init__(parent)
        self.api = api
        self._destination = destination        # album -> (folder, prefix, multi)
        self._sources = sources                # album -> the album's current folders
        self.album = None
        self._key = None                       # what the list shows: (album id, artist, target)
        self._token = 0
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.title = QtWidgets.QLabel('Already in your library — select an album')
        self.title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        layout.addWidget(self.title, 0)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 4)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open)
        layout.addWidget(self.table, 1)
        self._widths = self._saved_widths()
        self._sizing = False
        self.table.horizontalHeader().sectionResized.connect(self._resized)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(POLL_MS)

    # -- what to show ------------------------------------------------------------------------------

    def show_album(self, album):
        self.album = album
        self._poll()

    def _want(self):
        album = self.album
        if album is None or album.id not in self.api.tagger.albums:
            return None
        folder, _, _ = self._destination(album)
        artist = album.metadata['albumartist'] or album.metadata['artist']
        return (album.id, artist, folder or '') if artist else None

    def _poll(self):
        if not self.isVisible() and self._key is not None:
            return
        want = self._want()
        if want == self._key:
            return
        self._key = want
        self._token += 1
        if want is None:
            self.title.setText('Already in your library — select an album')
            self.table.setRowCount(0)
            return
        _, artist, folder = want
        roots = self.roots()
        if not roots:
            self.title.setText('Already in your library — set Options → File Naming → "Move files to" first')
            self.table.setRowCount(0)
            return
        self.title.setText('Already in your library — looking for %s...' % artist)
        self.title.setToolTip('Looking in:\n' + '\n'.join('%s: %s' % r for r in roots))
        sources = self._sources(self.album)
        thread.run_task(partial(scan, roots, artist, folder, sources),
                        partial(self._scanned, self._token, artist, roots))

    def roots(self):
        from picard.config import get_config
        return default_roots(get_config().setting['move_files_to'])

    def _scanned(self, token, artist, roots, result=None, error=None):
        if token != self._token:
            return                              # the selection moved on meanwhile
        self.table.setRowCount(0)
        if error:
            self.title.setText('Already in your library — could not read the library: %s' % error)
            return
        rows = result['rows']
        where = ' / '.join(label for label, _ in roots)
        if not result['artist_dirs']:
            self.title.setText('Already in your library — no %s folder in %s' % (artist, where))
            return
        urgent = [r for r in rows if r['class'] in (COLLISION, REDUNDANT)]
        dirs = ', '.join(sorted({os.path.basename(p) for _, p in result['artist_dirs']}))
        head = 'Already in your library — %s: %d album folder%s' % (dirs, len(rows), '' if len(rows) == 1 else 's')
        if any(r['class'] == COLLISION for r in rows):
            head += ' — ⛔ saving would merge into an existing folder'
        elif urgent:
            head += ' — you already have this album'
        self.title.setText(head)
        grey = self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = (r['where'], r['name'], '' if r['audio'] is None else str(r['audio']),
                     '' if r['images'] is None else str(r['images']), r['note'])
            colour = QtGui.QColor(COLOURS[r['class']]) if r['class'] in COLOURS else grey
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip('%s\n%s' % (r['path'], r['note']))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, r['path'])
                if r['class'] != OTHER or c == 0:
                    item.setForeground(colour if r['class'] != THIS else grey)
                if r['class'] in (COLLISION, REDUNDANT) and c == 1:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if c in (2, 3):
                    item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)
        self._size_columns()

    def _open(self, row, col):
        item = self.table.item(row, 0)
        path = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        if path:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    # -- column widths: the user's, kept across albums and restarts -----------------------------------

    def _saved_widths(self):
        try:
            saved = json.loads(self.api.plugin_config[COLUMNS_OPTION] or '[]')
        except (ValueError, KeyError, TypeError):
            return None
        return saved if isinstance(saved, list) and len(saved) == len(COLS) else None

    def _size_columns(self):
        self._sizing = True
        try:
            if self._widths:
                for c, w in enumerate(self._widths):
                    self.table.setColumnWidth(c, w)
            else:
                self.table.resizeColumnsToContents()
        finally:
            self._sizing = False

    def _resized(self, column, old, new):
        if self._sizing or column == len(COLS) - 1:
            return
        header = self.table.horizontalHeader()
        self._widths = [header.sectionSize(c) for c in range(len(COLS))]
        try:
            self.api.plugin_config[COLUMNS_OPTION] = json.dumps(self._widths)
        except (KeyError, RuntimeError):
            pass

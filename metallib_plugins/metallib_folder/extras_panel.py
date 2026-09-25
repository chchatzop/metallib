# MetalLib -- the Extra files panel (its own full-width row above the tag panel, user) and what
# happens to those files on save.
#
# For the selected album: every non-audio file in its folder(s), subfolders included. Tick = moves
# with the album; the name cell is editable (the library's "Front", "Back", "Booklet 03" ...), the
# "Becomes" column shows the full new name. Clicking a file opens the preview window (a separate,
# resizable window that remembers where it was; it follows the clicked file: images; .nfo/.txt/
# .log/.cue/.sfv/.m3u as text -- NFOs in the DOS character set, for their ASCII art).
# The Front (user): the image named "Front" is the album's embedded cover (shown in New Cover Art
# at once); an album without any image gets its cover (Metal Archives / MusicBrainz) written as
# "<prefix> - 00 - Front.jpg" when saved. Picard's own "move additional files" is replaced for album files:
# after the album's audio is saved, ticked files move under their new names, the rest goes to
# .metallib_trash (never audio), empty source folders are removed -- all logged, undoable from
# "Folder contents..." -> "Undo last clean-up". Choices are not saved (like the pressing lists).
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import re
from functools import partial

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.plugin3.api import (
    Album,
    File,
    Track,
)

from .extras import (
    IMAGE,
    TEXT,
    execute,
    foreign_audio,
    list_extras,
    plan,
    prefix_of,
    target_name,
)
from .folder_scan import (
    move_file,
    move_to_trash,
    new_batch,
)


USER_ATTR = 'metallib_extras_user'       # {path: {'tick': bool, 'stem': str}} on the Album
SAVE_ATTR = 'metallib_extras_save'       # the album's save state while its files save (see _saving)
LAYOUT_OPTION = 'extras_layout'
PREVIEW_OPTION = 'extras_preview_geometry'
COLUMNS_OPTION = 'extras_columns'
TEXT_LIMIT = 512 * 1024
_panel = None
_library = None                             # the "Already in your library" list beside it
_row = None                                 # the splitter holding both
_api = None
_log_factory = None
_originals = {}


# -- the album's extra files -----------------------------------------------------------------------------

def source_folders(files):
    folders = {os.path.dirname(f.filename) for f in files}
    # a disc folder ("CD1") belongs to its album folder: the parent holds the scans
    folders |= {os.path.dirname(d) for d in folders if len(os.path.basename(d)) <= 12
                and os.path.basename(d).lower().replace(' ', '').startswith(('cd', 'disc', 'disk'))}
    # a parent that is also listed covers its subfolders
    return sorted(d for d in folders if not any(d != o and d.startswith(o + os.sep) for o in folders))


def planned(album, folders=None):
    files = list(album.iterfiles())
    entries = list_extras(folders or source_folders(files), [f.filename for f in files])
    return plan(entries, getattr(album, USER_ATTR, None))


def destination(album):
    """(folder, prefix, multi) the album's files will get, from Picard's own naming."""
    files = list(album.iterfiles())
    if not files:
        return None, None, False
    f = files[0]
    try:
        new = f.make_filename(f.filename, f.metadata)
    except Exception:
        return None, None, False
    prefix, multi = prefix_of(new)
    return os.path.dirname(new), prefix, multi


# -- the Front ---------------------------------------------------------------------------------------------

_FRONT_FILE_RE = re.compile(r' - (?:\d+-)?00 - Front\.[A-Za-z]{3,4}$')


def front_entry(entries):
    return next((e for e in entries if e['tick'] and e['kind'] == IMAGE and e['stem'].lower() == 'front'), None)


def album_front(album):
    return next((img for img in album.metadata.images if img.is_front_image()), None)


def embed_front(album, entries):
    """The image named Front becomes the album's front cover (embedded when saved). -> True if changed."""
    e = front_entry(entries)
    if e is None:
        return False
    try:
        with open(e['path'], 'rb') as fh:
            data = fh.read()
    except OSError:
        return False
    current = album_front(album)
    if current is not None and current.data == data:
        return False
    from picard.coverart.image import CoverArtImage
    from picard.coverart.setters import (
        CoverArtSetter,
        CoverArtSetterMode,
    )
    image = CoverArtImage(url=QtCore.QUrl.fromLocalFile(e['path']).toString(), types=['front'], data=data)
    CoverArtSetter(CoverArtSetterMode.REPLACE, image, album).set_coverart()
    return True


def write_front_file(album, dest, prefix, multi):
    """No image moved with the album and none named Front in its folder: write the album's cover
    there as the library's Front file. -> the new path, or None."""
    try:
        if any(_FRONT_FILE_RE.search(f) for f in os.listdir(dest)):
            return None
    except OSError:
        return None
    image = album_front(album)
    data = image.data if image is not None else None
    if not data:
        return None
    path = os.path.join(dest, target_name(prefix, multi, 'Front', image.extension or '.jpg'))
    if os.path.exists(path):
        return None
    with open(path, 'wb') as fh:
        fh.write(data)
    return path


# -- preview ---------------------------------------------------------------------------------------------

def read_text(path):
    try:
        with open(path, 'rb') as fh:
            raw = fh.read(TEXT_LIMIT)
    except OSError as e:
        return str(e)
    if path.lower().endswith(('.nfo', '.diz')):
        return raw.decode('cp437', errors='replace')        # scene ASCII art
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def _mono():
    font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
    return font


class Preview(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        self.image = QtWidgets.QLabel()
        self.image.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(_mono())
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.none = QtWidgets.QLabel('Select a file to preview it.\nDouble-click: full size.')
        self.none.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        for w in (self.none, self.image, self.text):
            self.stack.addWidget(w)
        layout.addWidget(self.stack, 1)
        self.info = QtWidgets.QLabel()
        layout.addWidget(self.info, 0)
        self.pixmap = None

    def show_entry(self, e):
        self.pixmap = None
        if e is None:
            self.stack.setCurrentWidget(self.none)
            self.info.setText('')
            return
        if e['kind'] == IMAGE:
            reader = QtGui.QImageReader(e['path'])
            reader.setAutoTransform(True)
            img = reader.read()
            if img.isNull():
                self.stack.setCurrentWidget(self.none)
                self.info.setText('cannot read this image: %s' % reader.errorString())
                return
            self.pixmap = QtGui.QPixmap.fromImage(img)
            self.stack.setCurrentWidget(self.image)
            self._scale()
            self.info.setText('%d × %d px, %s' % (img.width(), img.height(), _size(e['size'])))
        elif e['kind'] == TEXT:
            self.text.setPlainText(read_text(e['path']))
            self.stack.setCurrentWidget(self.text)
            self.info.setText(_size(e['size']))
        else:
            self.stack.setCurrentWidget(self.none)
            self.info.setText('%s — no preview for this kind of file' % _size(e['size']))

    def _scale(self):
        if self.pixmap is not None:
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                                    QtCore.Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale()


class PreviewWindow(QtWidgets.QWidget):
    """The preview as its own window (user): pops up when a file is clicked, follows the clicked file,
    resizable (big = full size), remembers where it was. Closing it is fine: the next click reopens it."""

    def __init__(self, parent):
        super().__init__(parent, QtCore.Qt.WindowType.Tool)
        self.setWindowTitle('MetalLib — preview')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.preview = Preview()
        layout.addWidget(self.preview)
        self.resize(560, 620)
        self._restore()

    def show_entry(self, e):
        self.setWindowTitle('MetalLib — %s' % e['name'] if e else 'MetalLib — preview')
        self.preview.show_entry(e)
        if e is not None and not self.isVisible():
            self.show()

    def _restore(self):
        import base64
        try:
            geometry = _api.plugin_config[PREVIEW_OPTION]
            if geometry:
                self.restoreGeometry(QtCore.QByteArray(base64.b64decode(geometry)))
        except (KeyError, ValueError, TypeError):
            pass

    def _remember(self):
        import base64
        try:
            _api.plugin_config[PREVIEW_OPTION] = base64.b64encode(bytes(self.saveGeometry())).decode('ascii')
        except (KeyError, RuntimeError):
            pass

    def moveEvent(self, event):
        super().moveEvent(event)
        self._remember()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._remember()


def _size(n):
    return '%d B' % n if n < 10240 else ('%.0f KB' % (n / 1024) if n < 1048576 else '%.1f MB' % (n / 1048576))


# -- the panel -------------------------------------------------------------------------------------------

COLS = ('', 'File', 'New name', 'Becomes', 'Size', 'What')
C_TICK, C_FILE, C_STEM, C_BECOMES, C_SIZE, C_WHAT = range(6)


class ExtrasPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.album = None
        self.entries = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.title = QtWidgets.QLabel('Extra files — select an album')
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        layout.addWidget(self.title, 0)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
                                   | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 4)
        self.table.horizontalHeader().setStretchLastSection(True)
        # column widths: the user's, kept across albums and restarts (user); sized to the
        # contents only until the user has set them
        self._widths = self._saved_widths()
        self.table.horizontalHeader().sectionResized.connect(self._resized)
        self.table.itemChanged.connect(self._changed)
        self.table.currentCellChanged.connect(self._current)
        self.table.cellClicked.connect(self._clicked)
        self.preview = None                   # the preview window, made on the first click
        layout.addWidget(self.table, 1)
        self._filling = False

    def show_album(self, album):
        if album is not self.album:
            self.album = album
            self.refresh(select=0)

    def refresh(self, select=None):
        album = self.album
        current = self.table.currentRow() if select is None else select
        self._filling = True
        self.table.setRowCount(0)
        if album is None or album.id not in _api.tagger.albums:
            self.entries = []
            self.title.setText('Extra files — select an album')
            self._filling = False
            return
        self.entries = planned(album)
        embed_front(album, self.entries)
        dest, prefix, multi = destination(album)
        grey = self.palette().color(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text)
        self.table.setRowCount(len(self.entries))
        blocked = foreign_audio(self.entries)
        for r, e in enumerate(self.entries):
            tick = QtWidgets.QTableWidgetItem()
            tick.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
                          | QtCore.Qt.ItemFlag.ItemIsSelectable)
            tick.setCheckState(QtCore.Qt.CheckState.Checked if e['tick'] else QtCore.Qt.CheckState.Unchecked)
            self.table.setItem(r, C_TICK, tick)
            stem = QtWidgets.QTableWidgetItem(e['stem'])
            stem.setFlags(QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled
                          | QtCore.Qt.ItemFlag.ItemIsSelectable)
            stem.setToolTip('Type the name the library uses: Front, Back, CD, Inlay, Booklet 01 ...')
            self.table.setItem(r, C_STEM, stem)
            if e['tick']:
                becomes = target_name(prefix, multi, e['stem'], os.path.splitext(e['name'])[1]) if prefix else ''
            else:
                becomes = 'stays where it is' if _is_audio(e) else '→ trash'
            what = {'image': 'image', 'text': 'text'}.get(e['kind'], 'other')
            if e['kind'] == IMAGE and 'proof' in e['guess'].lower():
                what = 'image — proof?'
            if _is_audio(e):
                what = 'music not in this album — stays'
            for c, text in ((C_FILE, e['rel']), (C_BECOMES, becomes), (C_SIZE, _size(e['size'])), (C_WHAT, what)):
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
                if not e['tick']:
                    item.setForeground(grey)
                self.table.setItem(r, c, item)
        self._size_columns()
        moving = sum(1 for e in self.entries if e['tick'])
        trashed = sum(1 for e in self.entries if not e['tick'] and not _is_audio(e))
        where = source_folders(list(album.iterfiles()))
        head = 'Extra files in %s' % (where[0] if where else '?')
        if len(where) > 1:
            head += ' (+%d)' % (len(where) - 1)
        if not any(e['tick'] and e['kind'] == IMAGE for e in self.entries):
            cover = ' — no image: the album cover will be saved as Front'
        elif front_entry(self.entries):
            cover = ' — Front is the embedded cover'
        else:
            cover = ''
        if blocked:
            self.title.setText('%s — this folder has other music too: extra files are left alone%s' % (head, cover))
        elif not self.entries:
            self.title.setText('%s — none%s' % (head, cover))
        else:
            self.title.setText('%s — %d move with the album, %d to trash when saved%s'
                               % (head, moving, trashed, cover))
        self.title.setToolTip('\n'.join(where) + ('\n→ ' + dest if dest else ''))
        self._filling = False
        if self.entries:
            self.table.setCurrentCell(min(max(current, 0), len(self.entries) - 1), C_FILE)

    def _choice(self, e):
        user = getattr(self.album, USER_ATTR, None)
        if user is None:
            user = {}
            setattr(self.album, USER_ATTR, user)
        return user.setdefault(e['path'], {})

    def _changed(self, item):
        if self._filling or self.album is None or item.row() >= len(self.entries):
            return
        e = self.entries[item.row()]
        if item.column() == C_TICK:
            self._choice(e)['tick'] = item.checkState() == QtCore.Qt.CheckState.Checked
        elif item.column() == C_STEM:
            text = ' '.join(item.text().split()).strip(' -.')
            self._choice(e)['stem'] = text or e['guess']
        else:
            return
        QtCore.QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _saved_widths():
        import json
        try:
            saved = json.loads(_api.plugin_config[COLUMNS_OPTION] or '[]')
        except (ValueError, KeyError, TypeError):
            return None
        return saved if isinstance(saved, list) and len(saved) == len(COLS) else None

    def _size_columns(self):
        self._filling_widths = True
        try:
            if self._widths:
                for c, w in enumerate(self._widths):
                    self.table.setColumnWidth(c, w)
            else:
                self.table.resizeColumnsToContents()
        finally:
            self._filling_widths = False

    def _resized(self, column, old, new):
        if getattr(self, '_filling_widths', False) or column == len(COLS) - 1:
            return                            # the last column just fills the rest
        header = self.table.horizontalHeader()
        self._widths = [header.sectionSize(c) for c in range(len(COLS))]
        import json
        try:
            _api.plugin_config[COLUMNS_OPTION] = json.dumps(self._widths)
        except (KeyError, RuntimeError):
            pass

    def _show(self, row):
        if 0 <= row < len(self.entries):
            if self.preview is None:
                self.preview = PreviewWindow(self.window())
            self.preview.show_entry(self.entries[row])

    def _clicked(self, row, col):
        if col != C_TICK:                     # a click on the tick box only ticks
            self._show(row)

    def _current(self, row, col, prev_row, prev_col):
        # arrow keys: the open preview follows (it only pops up by itself on a click)
        if (not self._filling and row != prev_row and self.preview is not None
                and self.preview.isVisible()):
            self._show(row)


def _is_audio(e):
    from .folder_scan import AUDIO_EXTS
    return os.path.splitext(e['name'])[1].lower() in AUDIO_EXTS


def _album_of(objects):
    for obj in objects or []:
        if isinstance(obj, Album):
            return obj
        if isinstance(obj, Track):
            return obj.album
        if isinstance(obj, File) and isinstance(obj.parent_item, Track):
            return obj.parent_item.album
    return None


def _on_selection(objects):
    album = _album_of(objects)
    if album is not None and _panel is not None:
        _panel.show_album(album)
    if album is not None and _library is not None:
        _library.show_album(album)


# -- saving ------------------------------------------------------------------------------------------------

def _album_of_file(file):
    return file.parent_item.album if isinstance(file.parent_item, Track) else None


def _move_additional_files(self, old_filename, new_filename, config):
    # Album files: the Extra files step does it (names, ticks, trash). Anything else: Picard's way.
    if _album_of_file(self) is None:
        return _originals['move_additional_files'](self, old_filename, new_filename, config)


# One save of an album = the files whose save started together (pre-save runs on the main thread,
# file by file, as Picard starts them). The step runs once every one of them has FINISHED -- saved,
# failed or skipped (audit part 2 M2: waiting for post-save alone hung forever after a failed save,
# since Picard runs post-save processors only on success). Keyed by the file, not its album, since
# "Remove complete albums after saving" can take the album away before the post-save hooks run.
_saving = {}                                # id(file) -> the album's save state
_POLL_MS = 400


def on_file_saving(api, file):
    album = _album_of_file(file)
    if album is None:
        return
    st = getattr(album, SAVE_ATTR, None)
    if st is None:
        st = {'album': album, 'files': [], 'folders': set(), 'saved': []}
        setattr(album, SAVE_ATTR, st)
        embed_front(album, planned(album))    # the image named Front is the cover written into the tags
        QtCore.QTimer.singleShot(_POLL_MS, partial(_check_done, st))
    st['files'].append(file)
    st['folders'].update(source_folders([file]))
    _saving[id(file)] = st


def on_file_saved(api, file):
    st = _saving.pop(id(file), None)
    if st is not None:
        st['saved'].append(file.filename)


def _check_done(st):
    from picard.file import File
    still = [f for f in st['files'] if id(f) in _saving and f.state == File.State.PENDING]
    if still:
        QtCore.QTimer.singleShot(_POLL_MS, partial(_check_done, st))
        return
    album = st['album']
    failed = [f for f in st['files'] if id(f) in _saving]          # finished without a post-save
    for f in failed:
        _saving.pop(id(f), None)
    if getattr(album, SAVE_ATTR, None) is st:
        setattr(album, SAVE_ATTR, None)                              # the next save starts afresh
    if failed:
        _status('extra files not moved: %d track(s) did not save — fix that and save the album again'
                % len(failed))
        return
    saved = set(st['files'])
    old = [os.path.normcase(os.path.normpath(d)) for d in st['folders']]

    def in_old(path):
        d = os.path.normcase(os.path.dirname(path))
        return any(d == o or d.startswith(o + os.sep) for o in old)     # disc folders too
    waiting = [f for f in album.iterfiles() if f not in saved and in_old(f.filename)]
    if waiting:
        # a partial save: the rest of the album still lives in the old folder with the extras
        _status('extra files wait until the whole album is saved (%d track(s) still in the old folder)'
                % len(waiting))
        return
    run_extras(album, sorted(st['folders']), st['saved'])


def run_extras(album, folders, saved):
    """After the album's audio was saved: carry out the extra-files plan for its old folder(s), then
    make sure the new folder has a Front. `saved`: the new paths of the files this save wrote -- they
    name the destination (not album.iterfiles(): the album may be gone, or hold unsaved files)."""
    from picard.config import get_config
    setting = get_config().setting
    if not (setting['move_files'] or setting['rename_files']) or not saved:
        return
    prefix, multi = prefix_of(saved[0])
    dest = os.path.dirname(saved[0])
    if not prefix:
        _status('extra files left alone: the track names have no " - NN - " part to name them after')
        return
    entries = planned(album, folders)
    msg = []
    if entries and foreign_audio(entries):
        msg.append('extra files left alone: the old folder also holds music that is not in this album')
    elif entries:
        done = execute(entries, prefix, multi, dest, _log_factory(), new_batch(), move_file, move_to_trash)
        text = 'extra files: %d moved, %d to trash' % (len(done['moved']), len(done['trashed']))
        if done['errors']:
            text += ', %d left alone (%s)' % (len(done['errors']), done['errors'][0])
        msg.append(text + ' — undo: Folder contents... → Undo last clean-up')
    try:
        written = write_front_file(album, dest, prefix, multi)
    except OSError as e:
        written = None
        msg.append('the cover could not be saved as Front: %s' % e)
    if written:
        msg.append('cover saved as %s' % os.path.basename(written))
    if msg:
        _status('; '.join(msg))
    setattr(album, USER_ATTR, None)
    _panel_refresh(album)


def _status(text):
    _api.tagger.window.set_statusbar_message('MetalLib: %s', text)


def _panel_refresh(album):
    if _panel is not None and _panel.album is album:
        _panel.refresh()


# -- install ---------------------------------------------------------------------------------------------

def _restore_layout(splitters):
    import base64
    import json
    try:
        saved = json.loads(_api.plugin_config[LAYOUT_OPTION] or '{}')
    except (ValueError, KeyError, TypeError):
        return
    for s in splitters:
        state = saved.get(s.objectName())
        if state:
            s.restoreState(QtCore.QByteArray(base64.b64decode(state)))


def _save_layout(splitters, *args):
    import base64
    import json
    try:
        _api.plugin_config[LAYOUT_OPTION] = json.dumps(
            {s.objectName(): base64.b64encode(bytes(s.saveState())).decode('ascii') for s in splitters})
    except (KeyError, RuntimeError):
        pass


def install(api, log_factory, tries=100):
    global _panel, _library, _row, _api, _log_factory
    _api, _log_factory = api, log_factory
    window = getattr(api.tagger, 'window', None)
    if window is None or not hasattr(window, 'panel'):
        if tries:
            QtCore.QTimer.singleShot(200, partial(install, api, log_factory, tries - 1))
        return
    if _panel is not None:
        return
    rows = window.panel.parentWidget()        # Picard's vertical splitter: [panes, (pressings), tag panel]
    if not isinstance(rows, QtWidgets.QSplitter):
        return
    _panel = ExtrasPanel()
    from .library_panel import LibraryPanel
    _library = LibraryPanel(api, destination, lambda album: source_folders(list(album.iterfiles())))
    # its own full-width row right above the tag panel (user: names are wide), below the pressings;
    # split in two (user): extra files | already in your library
    _row = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
    _row.setObjectName('metallib_extras_library')
    _row.setChildrenCollapsible(False)
    _row.addWidget(_panel)
    _row.addWidget(_library)
    _row.setStretchFactor(0, 1)
    _row.setStretchFactor(1, 1)
    before = rows.sizes()
    rows.insertWidget(rows.count() - 1, _row)
    rows.setStretchFactor(rows.indexOf(_row), 0)
    if before and before[-1] > 300:
        # first layout (a saved one replaces it): a few rows' worth, taken from the tag panel
        sizes = before[:-1] + [140, before[-1] - 140]
        rows.setSizes(sizes)
    window.selection_updated.connect(_on_selection)
    splitters = (rows, _row)
    for delay in (0, 1500):                   # after Picard's and the pressing panel's own restore
        QtCore.QTimer.singleShot(delay, partial(_restore_layout, splitters))
    for sp in splitters:
        sp.splitterMoved.connect(partial(_save_layout, splitters))
    _originals['move_additional_files'] = File._move_additional_files
    File._move_additional_files = _move_additional_files


def uninstall():
    global _panel, _library, _row
    if 'move_additional_files' in _originals:
        File._move_additional_files = _originals.pop('move_additional_files')
    if _panel is None:
        return
    try:
        _api.tagger.window.selection_updated.disconnect(_on_selection)
    except (TypeError, RuntimeError):
        pass
    if _panel.preview is not None:
        _panel.preview.close()
    if _library is not None:
        _library.timer.stop()
    _row.setParent(None)
    _row.deleteLater()
    _panel = _library = _row = None

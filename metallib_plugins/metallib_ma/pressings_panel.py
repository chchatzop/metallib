# MetalLib -- the Pressings panel (a full-width row above the tag panel): one list per source (MusicBrainz, Metal
# Archives, Discogs) for the selected album. Exact track-count matches first, best on top; near
# misses greyed below; the pressing shown in the source's column is highlighted. Clicking another
# pressing loads it into that source's column only; New Value is then refreshed by the per-field
# rule (tags the user picked by hand stay). Choices are not saved (user, 2026-09-24).
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.metadata import Metadata
from picard.plugin3.api import (
    Album,
    File,
    Track,
)

from . import pools
from .pressings import (
    better_pick,
    judge,
    describe,
    rank,
    short_label,
    unmatched,
)


SOURCES = ('MusicBrainz', 'Metal Archives', 'Discogs')
# Two rows above every source's pressings (user). Exactly one row per list is active: one of these
# or one pressing. Not saved, like the pressing choice.
ALBUM_ONLY = '__album_only__'     # the source gives album facts, nothing pressing-specific
OFF = '__off__'                   # the source is not used at all
SPECIAL_ROWS = ((ALBUM_ONLY, 'Album info only (no pressing)'), (OFF, "Don't use this source"))
FILL_CAPS = {'Metal Archives': 40, 'Discogs': 25}     # pressing pages checked in the background
_ATTR = 'metallib_pressings'
_panel = None
_api = None


# -- state per album ----------------------------------------------------------------------------------

def state(album):
    st = getattr(album, _ATTR, None)
    if st is None:
        st = {s: {'items': [], 'chosen': None, 'extra': {}} for s in SOURCES}
        setattr(album, _ATTR, st)
    return st


def _seconds(track):
    # The user's file when there is one (that is what a pressing must fit), else the release's length.
    ms = (track.files[0].orig_metadata.length if track.files else 0) or track.metadata.length or 0
    return round(ms / 1000)


_REF_ATTR = 'metallib_pressings_ref'


def local_info(album):
    """What the pressings are compared with: the files' lengths (always current), and the album's
    catalog / media / year AS FIRST SEEN -- picking a pressing changes those in New Value, which
    must not re-rank the lists (a clicked pressing jumped to 2nd place, user)."""
    import os
    ref = getattr(album, _REF_ATTR, None)
    if ref is None and album.loaded:
        files = list(album.iterfiles())
        ref = {'folder': os.path.basename(os.path.dirname(files[0].filename)) if files else '',
               'catalog': album.metadata['catalognumber'], 'media': album.metadata['media'],
               'year': (album.metadata['originaldate'] or album.metadata['date'] or '')[:4]}
        setattr(album, _REF_ATTR, ref)
    info = dict(ref or {'folder': '', 'catalog': '', 'media': '', 'year': ''})
    info.update(track_count=len(album.tracks), lengths=[_seconds(t) for t in album.tracks])
    return info


def record(album, source, items, chosen=None, extra=None, fill=True):
    """Store the pressings a source offers (merging with what is known) and refresh the panel."""
    st = state(album)[source]
    known = {c['id']: c for c in st['items']}
    for c in items:
        old = known.get(c['id'])
        if old:
            c = {k: (v if v not in (None, '') else old.get(k)) for k, v in c.items()}
        known[c['id']] = c
    st['items'] = list(known.values())
    if chosen is not None and not st.get('user_picked'):
        st['chosen'] = str(chosen)
    _label_column(album, source)
    if extra:
        st['extra'].update(extra)
    if _api is not None:
        _api.logger.debug("pressings: %s lists %d for %s (shown: %s)", source, len(st['items']), album.id,
                          st['chosen'])
    refresh(album)
    if fill and source in FILL_CAPS:
        _start_fill(album, source)


def nothing_found(album, source):
    """The source was searched and has nothing for this album (the list says so instead of "yet")."""
    state(album)[source]['nothing'] = True
    refresh(album)


def note(album, source, cid, track_count=None, fits=None):
    """What a fetched pressing turned out to be (its track count, whether its lengths fit)."""
    for c in state(album)[source]['items']:
        if c['id'] == str(cid):
            if track_count is not None:
                c['track_count'] = track_count
            if fits is not None:
                c['fits'] = fits
    refresh(album)


def mode(album, source):
    """ALBUM_ONLY / OFF when the user picked one of the special rows for this source, else None."""
    chosen = state(album)[source]['chosen']
    return chosen if chosen in (ALBUM_ONLY, OFF) else None


def blocked(album, source):
    """A background lookup must not put a pressing into a column the user set to a special row."""
    return mode(album, source) is not None


def keep_user_pick(album, source):
    """A background result arrived for `source`. True when the user clicked a pressing there: the
    result is not used (audit part 1 L1). If the column lost the user's pressing meanwhile -- Refresh
    rebuilds the tracks -- that pressing is loaded back, so column and highlight agree."""
    st = state(album)[source]
    chosen = st.get('chosen')
    if not st.get('user_picked') or not chosen or chosen in (ALBUM_ONLY, OFF):
        return False
    if any(source not in (getattr(t, 'source_metadata', None) or {}) for t in album.tracks):
        load(album, source, chosen)
    return True


def set_mode(album, source, which):
    """The user picked "Album info only" / "Don't use this source": change that source's column
    on every track, then New Value by the rules (which fall back to the files' own values)."""

    from . import (
        _refresh_panel,
        apply_rules,
    )
    from .rules import PRESSING_TAGS
    st = state(album)[source]
    st['user_picked'] = True
    st['chosen'] = which
    for track in album.tracks:
        sources = getattr(track, 'source_metadata', None)
        if not sources or source not in sources:
            continue
        sources = dict(sources)            # a new dict: the tag panel may be reading the old one
        if which == OFF:
            del sources[source]
        else:
            md = Metadata()
            md.copy(sources[source])
            for tag in PRESSING_TAGS:
                if tag in md:
                    del md[tag]
            md['~source_label'] = 'album info only'
            sources[source] = md
        track.source_metadata = sources
    apply_rules(album)
    refresh(album)
    _refresh_panel()


def set_chosen(album, source, cid):
    state(album)[source]['chosen'] = str(cid)
    _label_column(album, source)
    refresh(album)


LABEL_TAG = '~source_label'     # read by the tag panel for the column header (metadatabox/sources.py)


def _label_column(album, source):
    """Name the shown pressing in the source's column header."""
    st = state(album)[source]
    c = next((c for c in st['items'] if c['id'] == st['chosen']), None)
    label = short_label(c) if c else ''
    changed = False
    for track in album.tracks:
        sources = getattr(track, 'source_metadata', None) or {}
        md = sources.get(source)
        if md is not None and label and md[LABEL_TAG] != label:
            new = Metadata()                # copies, not in place: the tag panel may be reading them
            new.copy(md)
            new[LABEL_TAG] = label
            track.source_metadata = {**sources, source: new}
            changed = True
    if changed and _api is not None:
        box = getattr(_api.tagger.window, 'metadata_box', None)
        if box is not None:
            box.update()


# -- background fill: track count + length fit ---------------------------------------------------------

def _start_fill(album, source):
    st = state(album)[source]
    if st.get('filling'):
        return
    # also pressings whose fit is unknown: every fit is judged against the FILES here
    todo = [c['id'] for c in st['items'] if c['track_count'] is None or c['fits'] is None][:FILL_CAPS[source]]
    if not todo:
        return
    st['filling'] = True
    lengths = local_info(album)['lengths']
    pools.run(source, partial(_fill, source, todo, lengths), partial(_filled, album, source))


def _fill(source, ids, lengths):
    """Thread: fetch each pressing's tracklist (cached afterwards) -> {id: (count, fits)}."""
    from . import (
        client,
        discogs,
    )
    from .discogs_release import flat_tracklist
    out = {}
    for cid in ids:
        try:
            if source == 'Metal Archives':
                secs = [t['length'] for t in client().album(cid)['tracks']]
            else:
                secs = [t['length'] for t in flat_tracklist(discogs().release(cid))]
        except Exception:
            continue
        out[cid] = (len(secs), judge(secs, lengths))
    return out


def _filled(album, source, result=None, error=None):
    st = state(album)[source]
    st['filling'] = False
    for c in st['items']:
        if result and c['id'] in result:
            c['track_count'], c['fits'] = result[c['id']]
    refresh(album)
    # The first lookup checks only a few pressings; now that all are known, a clearly better one
    # replaces the automatic choice -- never a pressing the user clicked.
    if not st.get('user_picked') and album.id in _api.tagger.albums:
        ranked = [c for c, _ in rank(st['items'], local_info(album))]
        better = better_pick(ranked, st['chosen'], len(album.tracks))
        if better:
            _api.logger.debug("pressings: %s switches to %s (fits, or has the album's track count)", source, better)
            load(album, source, better)


# -- loading a clicked pressing into its column ------------------------------------------------------

def load(album, source, cid):
    from . import _status
    # Only the pressing asked for LAST may land (audit part 1 L2): an automatic switch that finishes
    # after the user's click, or an earlier click that finishes later, is dropped in _apply.
    state(album)[source]['wanted'] = str(cid)
    _status('loading %s pressing %s...' % (source, cid))
    if source == 'Metal Archives':
        pools.run(pools.MA, partial(_fetch_ma, cid), partial(_loaded_ma, album, cid), pools.USER)
    elif source == 'Discogs':
        from . import discogs
        pools.run(pools.DISCOGS, partial(discogs().release, cid), partial(_loaded_dg, album, cid), pools.USER)
    else:
        from . import MB_INC
        _api.tagger.mb_api.get_release_by_id(cid, partial(_loaded_mb, album, cid), inc=MB_INC)


def _fetch_ma(cid):
    from . import client
    return client().album(cid)


def _loaded_ma(album, cid, result=None, error=None):
    from . import (
        METAL_ARCHIVES,
        _ma_fix,
        _status,
        apply_rules,
        attach,
        build_release,
        track_metadata,
    )
    if error or not result:
        _status('Metal Archives pressing %s could not be loaded: %s' % (cid, error))
        return
    extra = state(album)['Metal Archives']['extra']
    version = next((v for v in extra.get('versions', []) if str(v.get('album_id')) == str(cid)), None)
    _note_lengths(album, 'Metal Archives', cid, [t['length'] for t in result['tracks']])
    node = build_release(result, version, extra.get('original_date', ''))
    mds = track_metadata(node, fix=partial(_ma_fix, result['album_id'], dict(extra.get('band') or {}),
                                           result.get('lineup') or []))
    _apply(album, METAL_ARCHIVES, cid, mds, attach, apply_rules)


def _loaded_dg(album, cid, result=None, error=None):
    from . import (
        DISCOGS,
        _dg_fix,
        _status,
        apply_rules,
        attach,
        build_node,
        track_metadata,
    )
    if error or not result:
        _status('Discogs pressing %s could not be loaded: %s' % (cid, error))
        return
    from .discogs_release import flat_tracklist
    _note_lengths(album, 'Discogs', cid, [t['length'] for t in flat_tracklist(result)])
    built = build_node(result)
    mds = track_metadata(built['node'], fix=partial(_dg_fix, built))
    _apply(album, DISCOGS, cid, mds, attach, apply_rules)


def _loaded_mb(album, cid, document=None, http=None, error=None):
    from . import (
        MUSICBRAINZ,
        _mb_fix,
        _status,
        apply_rules,
        attach,
        track_metadata,
    )
    if error or not document:
        _status('MusicBrainz release %s could not be loaded' % cid)
        return
    mds = track_metadata(document, fix=partial(_mb_fix, document))
    secs = [round((t.get('length') or 0) / 1000) for m in document.get('media') or [] for t in m.get('tracks') or []]
    if not _note_lengths(album, 'MusicBrainz', cid, secs):
        for md in mds:                                  # never offer ids of a release that does not fit
            for tag in [t for t in md if t.startswith('musicbrainz_')]:
                del md[tag]
    _apply(album, MUSICBRAINZ, cid, mds, attach, apply_rules)


def _note_lengths(album, source, cid, secs):
    lengths = local_info(album)['lengths']
    fit = judge(secs, lengths)
    note(album, source, cid, len(secs), fit)
    return fit is True


def _apply(album, source, cid, mds, attach, apply_rules):
    from . import (
        _refresh_panel,
        _status,
    )
    if album.id not in _api.tagger.albums:
        return
    if state(album)[source].get('wanted') not in (None, str(cid)):
        return                          # another pressing was asked for since
    n = attach(album, source, mds)
    apply_rules(album)
    set_chosen(album, source, cid)
    _status('%s: pressing %s paired with %d of %d tracks' % (source, cid, n, len(album.tracks)))
    _refresh_panel()


# -- the widget ------------------------------------------------------------------------------------------

class _PathLabel(QtWidgets.QLabel):
    """One line; a long path is shortened in the MIDDLE so the album folder at its end stays
    readable; the full text is in the tooltip and can be selected/copied."""

    def __init__(self, text=''):
        super().__init__()
        self._full = ''
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_full(text)

    def set_full(self, text):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        width = max(50, self.width() - 4)
        self.setText(self.fontMetrics().elidedText(self._full, QtCore.Qt.TextElideMode.ElideMiddle, width))


def album_folders(album):
    """The folder(s) the album's files are in, most files first."""
    import collections
    import os
    count = collections.Counter(os.path.normpath(os.path.dirname(f.filename)) for f in album.iterfiles())
    return [d for d, _ in count.most_common()]


def folder_line(album):
    folders = album_folders(album)
    n = len(album.tracks)
    if not folders:
        return "No files — \"%s\", %d tracks" % (album.metadata["album"], n)
    more = ' (+%d more folder%s)' % (len(folders) - 1, 's' if len(folders) > 2 else '') if len(folders) > 1 else ''
    return "%s%s — %d tracks" % (folders[0], more, n)


def target_line(album):
    """The folder the album's files will get from Picard's naming (MetalLib's script), for the title
    line -- or why they stay."""
    if album.id not in _api.tagger.albums:
        return ''
    files = list(album.iterfiles())
    if not files:
        return ''
    from picard.config import get_config
    setting = get_config().setting
    if not (setting['move_files'] or setting['rename_files']):
        return 'files stay where they are (renaming and moving are off)'
    import os
    f = files[0]
    try:
        new = os.path.dirname(f.make_filename(f.filename, f.metadata))
    except Exception as e:                      # a naming script error: say so, don't break the panel
        return 'naming script error: %s' % e
    if os.path.normcase(new) == os.path.normcase(os.path.dirname(f.filename)):
        return 'stays in this folder'
    return new


class PressingsPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.album = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        # Title and list headers stay one text line high; all extra height goes to the lists (user).
        fixed = (QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
        self.title = _PathLabel('Pressings — select an album')
        # ... and after an arrow, the folder the album will be saved into -- live, as tags change (user)
        self.target = _PathLabel('')
        font = self.target.font()
        font.setBold(True)
        self.target.setFont(font)
        self.arrow = QtWidgets.QLabel('→')
        self.arrow.setSizePolicy(*fixed)
        line = QtWidgets.QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(self.title, 1)
        line.addWidget(self.arrow, 0)
        line.addWidget(self.target, 1)
        layout.addLayout(line, 0)
        self._target_timer = QtCore.QTimer(self)
        self._target_timer.timeout.connect(self._show_target)
        self._target_timer.start(1000)
        # A splitter, so the borders between the three lists can be dragged (user).
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setObjectName('metallib_pressings_lists')
        self.splitter.setChildrenCollapsible(False)
        self.lists = {}
        for source in SOURCES:
            column = QtWidgets.QWidget()
            col = QtWidgets.QVBoxLayout(column)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            head = QtWidgets.QLabel('<b>%s</b>' % source)
            head.setSizePolicy(*fixed)
            col.addWidget(head, 0)
            lst = QtWidgets.QListWidget()
            lst.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            lst.itemClicked.connect(partial(self._clicked, source))
            lst.setMinimumHeight(40)
            col.addWidget(lst, 1)
            self.lists[source] = lst
            column.setMinimumWidth(60)
            self.splitter.addWidget(column)
        layout.addWidget(self.splitter, 1)

    def show_album(self, album):
        self.album = album
        self.refresh()
        self._show_target()

    def _show_target(self):
        text = target_line(self.album) if self.album is not None and self.isVisible() else ''
        if text != self.target._full:
            self.target.set_full(text)
        self.arrow.setVisible(bool(text))

    def refresh(self):
        album = self.album
        if album is None or album.id not in _api.tagger.albums:
            self.title.set_full('Pressings — select an album')
            for lst in self.lists.values():
                lst.clear()
            return
        # The folder the selected album's files are in (user); the full path is in the tooltip.
        self.title.set_full(folder_line(album))
        local = local_info(album)
        red = QtGui.QBrush(QtGui.QColor(200, 0, 0))
        grey = QtGui.QBrush(self.palette().color(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text))
        for source, lst in self.lists.items():
            st = state(album)[source]
            lst.clear()
            ranked = rank(st['items'], local)
            for key, label in SPECIAL_ROWS:
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
                font = item.font()
                font.setItalic(True)
                font.setBold(st['chosen'] == key)
                item.setFont(font)
                item.setToolTip('Album facts only: no label, catalog, barcode, country, media, date or '
                                'release ids from %s' % source if key == ALBUM_ONLY
                                else '%s is not used for New Value at all' % source)
                if st['chosen'] == key:
                    item.setBackground(self.palette().highlight())
                    item.setForeground(self.palette().highlightedText())
                lst.addItem(item)
            line = QtWidgets.QListWidgetItem('─' * 40)
            line.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)       # a divider, not selectable
            lst.addItem(line)
            if not ranked:
                lst.addItem('(nothing found)' if st.get('nothing') else '(none found yet)')
                continue
            for c, greyed in ranked:
                text = describe(c, local['track_count'])
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, c['id'])
                tip = [text, '%s pressing %s%s' % (source, c['id'], ' — ' + c['desc'] if c['desc'] else '')]
                bad = unmatched(c, local['track_count'])
                if greyed:
                    tip.append('Different track count from your album')
                elif bad:
                    tip.append('Same track count, but its track lengths do not match your files')
                item.setToolTip('\n'.join(tip))
                if greyed:
                    item.setForeground(grey)
                elif bad:
                    item.setForeground(red)
                if st['chosen'] == c['id']:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setBackground(self.palette().highlight())
                    # still red when its lengths are unmatched, light enough to read on the highlight
                    item.setForeground(QtGui.QColor(255, 150, 150) if bad else self.palette().highlightedText())
                lst.addItem(item)

    def _clicked(self, source, item):
        cid = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not cid or self.album is None or cid == state(self.album)[source]['chosen']:
            return
        if cid in (ALBUM_ONLY, OFF):
            set_mode(self.album, source, cid)
            return
        state(self.album)[source]['user_picked'] = True
        load(self.album, source, cid)


def refresh(album):
    if _panel is not None and _panel.album is album:
        _panel.refresh()


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
    if album is not None and _panel is not None and album is not _panel.album:
        _panel.show_album(album)


ROW_NAME = 'metallib_panel_row'


def panel_row(window):
    """The full-width row between the file/album panes and the tag panel that holds MetalLib's
    panels side by side (pressings, extra files). Made by whichever plugin comes first."""
    for sp in window.findChildren(QtWidgets.QSplitter, ROW_NAME):
        return sp
    rows = window.panel.parentWidget()        # Picard's vertical splitter: [panes, tag panel]
    if not isinstance(rows, QtWidgets.QSplitter):
        return None
    before = rows.sizes()
    row = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
    row.setObjectName(ROW_NAME)
    row.setChildrenCollapsible(False)
    rows.insertWidget(rows.indexOf(window.panel) + 1, row)
    rows.setStretchFactor(rows.indexOf(row), 0)
    if len(before) == 2 and sum(before):
        # first layout: the panes keep most of their height, the row takes a slice of it
        panes, tags = before
        rows.setSizes([int(panes * 0.65), panes - int(panes * 0.65), tags])
    return row


def install(api, tries=100):
    """Put the panel across the whole window, between the file/album panes and the tag panel (user).
    Plugins start before the window exists, so this retries until it does."""
    global _panel, _api
    _api = api
    window = getattr(api.tagger, 'window', None)
    if window is None or not hasattr(window, 'panel'):
        if tries:
            QtCore.QTimer.singleShot(200, partial(install, api, tries - 1))
        return
    if _panel is not None:
        return
    row = panel_row(window)
    if row is None:
        return
    rows = row.parentWidget()
    _panel = PressingsPanel()
    row.insertWidget(0, _panel)               # pressings on the left, the Extra files panel beside it
    window.selection_updated.connect(_on_selection)
    splitters = (rows, row, _panel.splitter)
    _restore_layout(splitters)
    # Picard restores its own splitters when the window is shown, possibly after this: once more then.
    QtCore.QTimer.singleShot(0, partial(_restore_layout, splitters))
    for sp in splitters:
        sp.splitterMoved.connect(partial(_save_layout, splitters))


# Picard saves/restores splitter positions only for splitters that exist when its window opens;
# this panel is added a moment later, so it keeps its own: the panel's height and the widths of
# the three lists, saved whenever a border is dragged, restored on start.
LAYOUT_OPTION = 'pressings_layout'


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


def uninstall():
    """Take the panel out of the window again."""
    global _panel
    if _panel is None:
        return
    try:
        _api.tagger.window.selection_updated.disconnect(_on_selection)
    except (TypeError, RuntimeError):
        pass
    row = _panel.parentWidget()
    _panel.setParent(None)
    _panel.deleteLater()
    _panel = None
    if row is not None and row.objectName() == ROW_NAME and row.count() == 0:
        row.setParent(None)
        row.deleteLater()


__all__ = ['install', 'note', 'record', 'set_chosen', 'uninstall']

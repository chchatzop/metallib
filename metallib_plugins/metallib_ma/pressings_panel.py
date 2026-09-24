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

from picard.plugin3.api import (
    Album,
    File,
    Track,
)
from picard.util import thread

from .pressings import (
    better_pick,
    describe,
    rank,
    short_label,
    unmatched,
)


SOURCES = ('MusicBrainz', 'Metal Archives', 'Discogs')
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


def local_info(album):
    import os
    files = list(album.iterfiles())
    return {'track_count': len(album.tracks),
            'lengths': [_seconds(t) for t in album.tracks],
            'folder': os.path.basename(os.path.dirname(files[0].filename)) if files else '',
            'catalog': album.metadata['catalognumber'], 'media': album.metadata['media'],
            'year': (album.metadata['originaldate'] or album.metadata['date'] or '')[:4]}


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
    if chosen is not None:
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


def note(album, source, cid, track_count=None, fits=None):
    """What a fetched pressing turned out to be (its track count, whether its lengths fit)."""
    for c in state(album)[source]['items']:
        if c['id'] == str(cid):
            if track_count is not None:
                c['track_count'] = track_count
            if fits is not None:
                c['fits'] = fits
    refresh(album)


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
        md = (getattr(track, 'source_metadata', None) or {}).get(source)
        if md is not None and label and md[LABEL_TAG] != label:
            md[LABEL_TAG] = label
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
    todo = [c['id'] for c in st['items'] if c['track_count'] is None][:FILL_CAPS[source]]
    if not todo:
        return
    st['filling'] = True
    lengths = local_info(album)['lengths']
    thread.run_task(partial(_fill, source, todo, lengths), partial(_filled, album, source))


def _fill(source, ids, lengths):
    """Thread: fetch each pressing's tracklist (cached afterwards) -> {id: (count, fits)}."""
    from . import (
        client,
        discogs,
    )
    from .discogs_release import flat_tracklist
    from .ma_release import fits
    out = {}
    for cid in ids:
        try:
            if source == 'Metal Archives':
                secs = [t['length'] for t in client().album(cid)['tracks']]
            else:
                secs = [t['length'] for t in flat_tracklist(discogs().release(cid))]
        except Exception:
            continue
        out[cid] = (len(secs), fits(secs, lengths) if len(secs) == len(lengths) else False)
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
    _status('loading %s pressing %s...' % (source, cid))
    if source == 'Metal Archives':
        thread.run_task(partial(_fetch_ma, cid), partial(_loaded_ma, album, cid))
    elif source == 'Discogs':
        from . import discogs
        thread.run_task(partial(discogs().release, cid), partial(_loaded_dg, album, cid))
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
    from .ma_release import fits
    lengths = local_info(album)['lengths']
    ok = bool(fits(secs, lengths)) if len(secs) == len(lengths) else False
    note(album, source, cid, len(secs), ok)
    return ok


def _apply(album, source, cid, mds, attach, apply_rules):
    from . import (
        _refresh_panel,
        _status,
    )
    if album.id not in _api.tagger.albums:
        return
    n = attach(album, source, mds)
    apply_rules(album)
    set_chosen(album, source, cid)
    _status('%s: pressing %s paired with %d of %d tracks' % (source, cid, n, len(album.tracks)))
    _refresh_panel()


# -- the widget ------------------------------------------------------------------------------------------

class PressingsPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.album = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        # Title and list headers stay one text line high; all extra height goes to the lists (user).
        fixed = (QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
        self.title = QtWidgets.QLabel('Pressings — select an album')
        self.title.setSizePolicy(*fixed)
        layout.addWidget(self.title, 0)
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

    def refresh(self):
        album = self.album
        if album is None or album.id not in _api.tagger.albums:
            self.title.setText('Pressings — select an album')
            for lst in self.lists.values():
                lst.clear()
            return
        self.title.setText('Pressings of "%s" — %d tracks here; click one to load it into its column'
                           % (album.metadata['album'], len(album.tracks)))
        local = local_info(album)
        red = QtGui.QBrush(QtGui.QColor(200, 0, 0))
        grey = QtGui.QBrush(self.palette().color(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text))
        for source, lst in self.lists.items():
            st = state(album)[source]
            lst.clear()
            ranked = rank(st['items'], local)
            if not ranked:
                lst.addItem('(none found yet)')
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
        if cid and self.album is not None and cid != state(self.album)[source]['chosen']:
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
    rows = window.panel.parentWidget()        # Picard's vertical splitter: [panes, tag panel]
    if not isinstance(rows, QtWidgets.QSplitter):
        return
    before = rows.sizes()
    _panel = PressingsPanel()
    rows.insertWidget(rows.indexOf(window.panel) + 1, _panel)
    rows.setStretchFactor(rows.indexOf(_panel), 0)
    if len(before) == 2 and sum(before):
        # first layout: the panes keep most of their height, the panel takes a slice of it
        panes, tags = before
        rows.setSizes([int(panes * 0.65), panes - int(panes * 0.65), tags])
    window.selection_updated.connect(_on_selection)
    splitters = (rows, _panel.splitter)
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
    _panel.setParent(None)
    _panel.deleteLater()
    _panel = None


__all__ = ['install', 'note', 'record', 'set_chosen', 'uninstall']

# MetalLib Metal Archives -- load a cluster as a Metal Archives album.
#
# Right-click a cluster -> "Load from Metal Archives":
#   1. search MA by the cluster's band + album; several hits -> you pick
#   2. pick the pressing: catalog number in the folder name / tag, else same media, else all;
#      a pressing "fits" when its track count and durations match the files. Exactly one fitting
#      pressing is taken automatically; otherwise you pick (user rule 2026-09-23)
#   3. build a Picard album from that pressing's own tracklist (discs, numbers, lengths, bonus
#      tracks), label, catalog, date and release type; the files are then placed by
#      title + duration (MetalLib Tracks plugin) and the MA cover is attached.
#
# Network work runs in Picard's thread pool; albums and dialogs only on the main thread.
# No fake MusicBrainz id is ever written: every musicbrainz_* tag of an MA album is deleted.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial
import os

from PyQt6 import QtWidgets

from picard.album import (
    Album,
    AlbumStatus,
)
from picard.metadata import Metadata
from picard.plugin3.api import (
    BaseAction,
    Cluster,
    PluginApi,
)
from picard.util import thread

from .ma_client import (
    MAClient,
    MAError,
)
from .ma_release import (
    album_id_for,
    build_release,
    fits,
    ma_date,
    narrow_pressings,
)


MAX_PRESSING_FETCHES = 12       # pressing pages fetched to judge fit (each ~1.5 s, then cached)
FAKE_ID_TAGS = ('musicbrainz_albumid', 'musicbrainz_releasegroupid', 'musicbrainz_recordingid',
                'musicbrainz_trackid', 'musicbrainz_artistid', 'musicbrainz_albumartistid',
                'musicbrainz_discid', 'musicbrainz_releasetrackid')

_api = None
_client = None


def client():
    global _client
    if _client is None:
        # Next to Picard's plugin folder (dev launcher: .devdata\metallib), never in the plugin's
        # own source folder, which is the git repo for a local install.
        from picard.const.appdirs import plugin_folder
        folder = os.path.join(os.path.dirname(os.path.abspath(plugin_folder())), 'metallib')
        os.makedirs(folder, exist_ok=True)
        _client = MAClient(os.path.join(folder, 'ma_cache.sqlite'))
    return _client


# ------------------------------------------------------------------------------------------------
# The album
# ------------------------------------------------------------------------------------------------

class MetalArchivesAlbum(Album):
    """An album built from Metal Archives data instead of a MusicBrainz release."""

    def __init__(self, album_id, release_node, ma_info):
        super().__init__(album_id)
        self._ma_node = release_node
        self.ma_info = ma_info          # {'album_id', 'band_id', 'url', 'cover_url'}

    def load(self, priority=False, refresh=False):
        # Replaces the MusicBrainz request: parse the synthetic node with Picard's own parser.
        self.loaded = False
        self.status = AlbumStatus.LOADING
        self.metadata.clear()
        self.genres.clear()
        self.clear_errors()
        self._new_metadata = Metadata()
        self._new_tracks = []
        self._pending_tasks.clear()
        try:
            self._parse_release(self._ma_node)
            if self.release_group is not None:
                self.release_group.loaded = True        # no MB "other versions" browse
            self._strip_fake_ids(self._new_metadata)
            self._new_metadata['~ma_album_id'] = self.ma_info['album_id']
            self._new_metadata['~ma_band_id'] = self.ma_info['band_id']
            self._apply_band(self._new_metadata)
            self._finalize_loading(False)
        except Exception:
            import traceback
            self.error_append(traceback.format_exc())
            self._finalize_loading(True)

    def _finalize_loading_track(self, *args, **kwargs):
        track = super()._finalize_loading_track(*args, **kwargs)
        self._strip_fake_ids(track.metadata)
        track.metadata['~ma_album_id'] = self.ma_info['album_id']
        self._apply_band(track.metadata)
        # What Metal Archives says, shown as its own column in the tag panel (metadatabox/sources.py).
        ma = Metadata()
        ma.copy(track.metadata)
        sources = getattr(track, 'source_metadata', None) or {}
        sources['Metal Archives'] = ma
        track.source_metadata = sources
        return track

    def _apply_band(self, metadata):
        # Band page facts. Genre becomes the genre tag (MA's wording, e.g. "Black Metal");
        # the country is the BAND's, not the release's, so it is only exposed to scripts
        # (%_ma_band_country% "Italy", %_ma_band_country_code% "IT" -- as in "Aghar (IT)").
        band = self.ma_info.get('band') or {}
        if band.get('genre'):
            metadata['genre'] = band['genre']
        for key in ('country', 'country_code', 'status', 'formed'):
            if band.get(key):
                metadata['~ma_band_' + key] = band[key]

    @staticmethod
    def _strip_fake_ids(metadata):
        for tag in FAKE_ID_TAGS:
            if tag in metadata:
                del metadata[tag]       # deleted -> also removed from files on save

    @property
    def can_refresh(self):
        return False

    @property
    def can_browser_lookup(self):
        return False


# ------------------------------------------------------------------------------------------------
# Background work (thread pool) -- no Qt objects touched here
# ------------------------------------------------------------------------------------------------

def _search(band, album):
    hits = client().search_albums(band, album)
    if not hits and album:
        hits = client().search_albums(band, '')         # album title spelled differently on MA
    return hits


def _resolve(hit, local):
    """Fetch the album, its versions and (some of) the pressing pages; decide the pressing."""
    base = client().album(hit['album_id'])
    try:
        versions = client().versions(hit['album_id'])
    except MAError:
        versions = []
    if not versions:
        versions = [{'album_id': base['album_id'], 'date': base['date'], 'label': base['label'],
                     'catalog': base['catalog'], 'format': base['format'], 'desc': ''}]
    candidates, why = narrow_pressings(versions, local['folder'], local['catalog'], local['media'])
    checked = []
    for v in candidates[:MAX_PRESSING_FETCHES]:
        page = base if v['album_id'] == base['album_id'] else client().album(v['album_id'])
        lengths = [t['length'] for t in page['tracks']]
        checked.append({'version': v, 'page': page, 'fits': fits(lengths, local['lengths'])})
    original = min((ma_date(v['date']) for v in versions if ma_date(v['date'])), default='')
    try:
        band = client().band(base['band_id'] or hit['band_id'])
    except MAError:
        band = {}
    band['country_code'] = hit.get('band_country', '')
    return {'base': base, 'versions': versions, 'checked': checked, 'narrowed_by': why,
            'original_date': original, 'more': len(candidates) > MAX_PRESSING_FETCHES, 'band': band}


# ------------------------------------------------------------------------------------------------
# Main-thread flow
# ------------------------------------------------------------------------------------------------

def _local_info(cluster):
    files = list(cluster.iterfiles())
    first = files[0] if files else None
    return {
        'files': files,
        'band': cluster.metadata['albumartist'] or (first.orig_metadata['albumartist'] if first else ''),
        'album': cluster.metadata['album'] or (first.orig_metadata['album'] if first else ''),
        'folder': os.path.basename(os.path.dirname(first.filename)) if first else '',
        'catalog': first.orig_metadata['catalognumber'] if first else '',
        'media': first.orig_metadata['media'] if first else '',
        'lengths': [round((f.orig_metadata.length or 0) / 1000) for f in files],
    }


def _status(text):
    _api.tagger.window.set_statusbar_message('MetalLib: %s', text)


def start_lookup(cluster):
    local = _local_info(cluster)
    if not local['files']:
        return
    _status('searching Metal Archives for "%s" by %s...' % (local['album'], local['band']))
    thread.run_task(partial(_search, local['band'], local['album']),
                    partial(_on_search, cluster, local))


def _on_search(cluster, local, result=None, error=None):
    if error:
        _status('Metal Archives search failed: %s' % error)
        return
    if not result:
        _status('nothing found on Metal Archives for "%s" by %s' % (local['album'], local['band']))
        return
    exact = [h for h in result if h['album'].lower() == local['album'].lower()
             and h['band'].lower() == local['band'].lower()]
    pool = exact or result
    if len(pool) == 1:
        hit = pool[0]
    else:
        rows = [(h['band'], h['band_country'], h['album'], h['type']) for h in pool]
        i = _pick('Which Metal Archives album?', ('Band', 'Country', 'Album', 'Type'), rows)
        if i is None:
            return
        hit = pool[i]
    _status('loading "%s" and its pressings from Metal Archives...' % hit['album'])
    thread.run_task(partial(_resolve, hit, local), partial(_on_resolved, cluster, local, hit))


def _on_resolved(cluster, local, hit, result=None, error=None):
    if error:
        _status('Metal Archives lookup failed: %s' % error)
        return
    fitting = [c for c in result['checked'] if c['fits']]
    if len(fitting) == 1 and not result['more']:
        chosen = fitting[0]
    else:
        rows = [(c['version']['date'], c['version']['label'], c['version']['catalog'],
                 c['version']['format'], c['version']['desc'],
                 '%d tracks%s' % (len(c['page']['tracks']), ' — fits' if c['fits'] else ''))
                for c in result['checked']]
        title = ('No pressing fits your %d files exactly — pick one' % len(local['files'])
                 if not fitting else 'Several pressings fit — pick one')
        i = _pick(title, ('Date', 'Label', 'Catalog', 'Format', 'Description', 'Tracks'), rows,
                  preselect=result['checked'].index(fitting[0]) if fitting else 0)
        if i is None:
            return
        chosen = result['checked'][i]
    _build_album(cluster, local, hit, chosen, result['original_date'], result.get('band') or {})


def _build_album(cluster, local, hit, chosen, original_date, band):
    tagger = _api.tagger
    page, version = chosen['page'], chosen['version']
    node = build_release(page, version, original_date)
    aid = album_id_for(page['album_id'])
    album = tagger.albums.get(aid)
    if album is None:
        album = MetalArchivesAlbum(aid, node, {
            'album_id': page['album_id'], 'band_id': page['band_id'] or hit['band_id'],
            'cover_url': page['cover_url'], 'band': band})
        tagger.albums[aid] = album
        tagger.album_added.emit(album)
    # Files first (they wait in "unmatched" while not loaded), then load: the load's own
    # match_files places every file by title + duration.
    tagger.move_files_to_album(local['files'], album=album)
    if not album.loaded:
        album.load()
    _status('loaded "%s" (%s %s) from Metal Archives' % (page['album'], version.get('format', ''),
                                                          version.get('catalog', '')))
    if page['cover_url']:
        thread.run_task(partial(client().fetch_bytes, page['cover_url']), partial(_on_cover, album))


def _on_cover(album, result=None, error=None):
    if error or not result or album.id not in _api.tagger.albums:
        return
    from picard.coverart.image import CoverArtImage
    from picard.coverart.setters import (
        CoverArtSetter,
        CoverArtSetterMode,
    )
    image = CoverArtImage(url=album.ma_info['cover_url'], types=['front'], data=result)
    CoverArtSetter(CoverArtSetterMode.REPLACE, image, album, update_orig=True).set_coverart()


def _pick(title, headers, rows, preselect=0):
    """Modal list picker; returns the chosen row index or None."""
    dialog = QtWidgets.QDialog(_api.tagger.window)
    dialog.setWindowTitle('MetalLib — ' + title)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(title))
    table = QtWidgets.QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QtWidgets.QTableWidgetItem(str(value)))
    table.resizeColumnsToContents()
    table.selectRow(preselect)
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


class LoadFromMetalArchives(BaseAction):
    TITLE = "Load from Metal Archives"

    def callback(self, objs):
        for obj in objs:
            if isinstance(obj, Cluster) and not obj.special:
                start_lookup(obj)


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    api.register_cluster_action(LoadFromMetalArchives)
    for name, doc in (('_ma_band_country', 'Band country from Metal Archives, e.g. "Italy".'),
                      ('_ma_band_country_code', 'Band country code from Metal Archives, e.g. "IT".'),
                      ('_ma_band_status', 'Band status from Metal Archives, e.g. "Active".'),
                      ('_ma_band_formed', 'Year the band formed, from Metal Archives.'),
                      ('_ma_album_id', 'Metal Archives album (pressing) id.'),
                      ('_ma_band_id', 'Metal Archives band id.')):
        api.register_script_variable(name, documentation=doc)

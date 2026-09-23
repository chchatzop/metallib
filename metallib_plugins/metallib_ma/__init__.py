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
import re

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
    auto_pick,
    build_release,
    fits,
    format_kind,
    lineup_tags,
    ma_date,
    mb_genres,
    narrow_pressings,
    rank_hits,
    title_score,
    track_count_compatible,
)
from .rules import choose
from .source_columns import (
    METAL_ARCHIVES,
    MUSICBRAINZ,
    ShadowAlbum,
    attach,
    set_own_source,
    track_metadata,
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
        _apply_lineup(self.ma_info.get('lineup'), track.metadata)
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
    if not hits and album and re.search(r'\band\b', album, re.I):
        hits = client().search_albums(band, re.sub(r'\band\b', '&', album, flags=re.I))   # "And" vs "&"
    if not hits and album:
        hits = client().search_albums(band, '')         # title spelled differently on MA: all of the band
    return hits


def _resolve(hit, local, max_fetches=MAX_PRESSING_FETCHES):
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
    for v in candidates[:max_fetches]:
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
            'original_date': original, 'more': len(candidates) > max_fetches, 'band': band}


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
    ranked = rank_hits(result, local['band'], local['album'])
    hit = auto_pick(ranked)
    if hit is None:
        # Best matches first, the best one preselected.
        rows = [(h['band'], h['band_country'], h['album'], h['type'], '%d%%' % min(100, round(score * 100)))
                for score, h in ranked]
        i = _pick('Which Metal Archives album?', ('Band', 'Country', 'Album', 'Type', 'Title match'), rows)
        if i is None:
            return
        hit = ranked[i][1]
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
        # Best match first: pressings that fit the files, then the rest; the first is preselected.
        ordered = fitting + [c for c in result['checked'] if not c['fits']]
        rows = [(c['version']['date'], c['version']['label'], c['version']['catalog'],
                 c['version']['format'], c['version']['desc'],
                 '%d tracks%s' % (len(c['page']['tracks']), ' — fits' if c['fits'] else ''))
                for c in ordered]
        title = ('No pressing fits your %d files exactly — pick one' % len(local['files'])
                 if not fitting else 'Several pressings fit — pick one')
        i = _pick(title, ('Date', 'Label', 'Catalog', 'Format', 'Description', 'Tracks'), rows)
        if i is None:
            return
        chosen = ordered[i]
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
            'cover_url': page['cover_url'], 'band': band, 'lineup': page.get('lineup') or []})
        tagger.albums[aid] = album
        tagger.album_added.emit(album)
    # Files first (they wait in "unmatched" while not loaded), then load: the load's own
    # match_files places every file by title + duration.
    tagger.move_files_to_album(local['files'], album=album)
    if not album.loaded:
        album.load()
    _status('loaded "%s" (%s %s) from Metal Archives' % (page['album'], version.get('format', ''),
                                                          version.get('catalog', '')))
    start_mb_lookup(album, page['band'], page['album'])
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


# ------------------------------------------------------------------------------------------------
# The other source for the side-by-side columns
# ------------------------------------------------------------------------------------------------

BACKGROUND_PRESSING_FETCHES = 4     # background MA lookups for MB albums fetch fewer pressing pages
MB_RELEASE_FETCHES = 3              # MB release candidates fetched to find one that fits
# Everything MusicBrainz has for a release (Picard's own full set incl. relationships), except the
# user's own tags and ratings. Which relationships become tags still follows Picard's options.
MB_INC = ('aliases', 'annotation', 'artist-credits', 'artists', 'discids', 'genres', 'isrcs', 'labels',
          'media', 'recordings', 'release-groups', 'artist-rels', 'recording-rels', 'label-rels',
          'release-group-level-rels', 'release-rels', 'series-rels', 'url-rels', 'work-rels',
          'recording-level-rels', 'work-level-rels')


def _refresh_panel():
    box = getattr(_api.tagger.window, 'metadata_box', None)
    if box is not None:
        box.update()


def _when_loaded(album, func, tries=200):
    """Run func() once `album` finished loading (it may still be loading when a lookup returns)."""
    if album.id not in _api.tagger.albums:
        return
    if album.loaded:
        func()
    elif tries:
        from PyQt6 import QtCore
        QtCore.QTimer.singleShot(300, partial(_when_loaded, album, func, tries - 1))


# -- MusicBrainz album: its own values + Metal Archives in the background ------------------------

def on_track_built(api, track, metadata, track_node, release_node=None):
    # What MusicBrainz said, before scripts or the user change anything: the MB column.
    if isinstance(track.album, (MetalArchivesAlbum, ShadowAlbum)):
        return
    set_own_source(track, MUSICBRAINZ, metadata)
    if release_node:
        _mb_fix(release_node, track.source_metadata[MUSICBRAINZ])


def _mb_fix(release_node, md):
    # Picard only fills genre when its Genres option is on; the column shows MB's genre regardless
    # (only present when the release was requested with genres).
    if 'genre' not in md:
        genres = mb_genres(release_node)
        if genres:
            md['genre'] = genres


def on_mb_album(api, album, metadata, release_node):
    if isinstance(album, (MetalArchivesAlbum, ShadowAlbum)):
        return
    band, title = metadata['albumartist'], metadata['album']
    media = release_node.get('media') or []
    local = {'folder': '', 'catalog': metadata['catalognumber'],
             'media': (media[0].get('format') or '') if media else '',
             'lengths': [round((t.get('length') or 0) / 1000) for m in media for t in m.get('tracks') or []]}
    thread.run_task(partial(_background_ma, band, title, local), partial(_on_background_ma, album))


def _background_ma(band, title, local):
    """Thread: find the MA album and pressing without asking anything."""
    pick = auto_pick(rank_hits(_search(band, title), band, title))
    if pick is None:
        return None
    result = _resolve(pick, local, max_fetches=BACKGROUND_PRESSING_FETCHES)
    fitting = [c for c in result['checked'] if c['fits']]
    chosen = fitting[0] if fitting else (result['checked'][0] if result['checked'] else None)
    return {'hit': pick, 'result': result, 'chosen': chosen} if chosen else None


def _on_background_ma(album, result=None, error=None):
    if error or not result:
        if error:
            _api.logger.warning("background Metal Archives lookup failed: %s", error)
        return
    page, version = result['chosen']['page'], result['chosen']['version']
    node = build_release(page, version, result['result']['original_date'])
    band = dict(result['result'].get('band') or {})
    mds = track_metadata(node, fix=partial(_ma_fix, page['album_id'], band, page.get('lineup') or []))

    def apply():
        n = attach(album, METAL_ARCHIVES, mds)
        apply_rules(album)
        _status('Metal Archives: "%s" (%s) paired with %d of %d tracks'
                % (page['album'], version.get('format', ''), n, len(album.tracks)))
        _refresh_panel()
    _when_loaded(album, apply)


def _ma_fix(ma_album_id, band, lineup, md):
    MetalArchivesAlbum._strip_fake_ids(md)
    md['~ma_album_id'] = ma_album_id
    if band.get('genre'):
        md['genre'] = band['genre']
    _apply_lineup(lineup, md)


def _apply_lineup(lineup, md):
    # Band members / guests / staff from MA's lineup tab as performer:<instrument>, lyricist,
    # composer, writer, producer, engineer, mixer -- per track, honouring "(tracks 1-8, 10)".
    try:
        position = int(md['~absolutetracknumber'] or md['tracknumber'] or 0)
    except ValueError:
        position = 0
    for tag, names in lineup_tags(lineup, position).items():
        md[tag] = names


# -- Metal Archives album: find the same release on MusicBrainz ---------------------------------

def start_mb_lookup(album, band, title):
    count = len(album.tracks) or sum(len(m.get('tracks') or []) for m in album._ma_node.get('media') or [])
    _api.tagger.mb_api.find_releases(partial(_on_mb_search, album, band, title, count),
                                     artist=band, release=title, limit=10)


def _on_mb_search(album, band, title, count, document=None, http=None, error=None):
    if error or not document:
        return
    # Several MB releases usually share a tracklist (CD, reissues, digital), so durations alone
    # cannot tell them apart: prefer the one on the MA pressing's media, then its year.
    want_media = format_kind(album.metadata['media'])
    want_year = (album.metadata['date'] or '')[:4]
    cands = []
    for r in document.get('releases') or []:
        credit = ''.join(c.get('name', '') + c.get('joinphrase', '') for c in r.get('artist-credit') or [])
        n = r.get('track-count') or sum(m.get('track-count') or 0 for m in r.get('media') or [])
        score = title_score(r.get('title'), title)
        if score >= 0.8 and title_score(credit, band) >= 0.8 and track_count_compatible(n, count):
            media = {format_kind(m.get('format')) for m in r.get('media') or []}
            score += 0.1 if n == count else 0
            score += 0.3 if want_media and want_media in media else 0
            score += 0.2 if want_year and (r.get('date') or '')[:4] == want_year else 0
            cands.append((score, r['id']))
    cands.sort(key=lambda c: -c[0])
    ids = [rid for _, rid in cands[:MB_RELEASE_FETCHES]]
    if ids:
        _fetch_mb_release(album, ids, [])
    else:
        _status('MusicBrainz has no release matching "%s" by %s' % (title, band))


def _fetch_mb_release(album, ids, fetched):
    _api.tagger.mb_api.get_release_by_id(ids[0], partial(_on_mb_release, album, ids[1:], fetched), inc=MB_INC)


def _on_mb_release(album, rest, fetched, document=None, http=None, error=None):
    if not error and document:
        fetched.append(document)
        album_lengths = [round((t.metadata.length or 0) / 1000) for t in album.tracks]
        node_lengths = [round((t.get('length') or 0) / 1000)
                        for m in document.get('media') or [] for t in m.get('tracks') or []]
        if fits(node_lengths, album_lengths):
            return _use_mb_release(album, document)
    if rest:
        return _fetch_mb_release(album, rest, fetched)
    if fetched:
        _use_mb_release(album, fetched[0], fitted=False)


def _use_mb_release(album, node, fitted=True):
    mds = track_metadata(node, fix=partial(_mb_fix, node))
    if not fitted:
        # A release whose durations do not fit may be another pressing: show its values, but
        # never offer its MusicBrainz ids (they would tie the files to the wrong release).
        for md in mds:
            for tag in [t for t in md if t.startswith('musicbrainz_')]:
                del md[tag]

    def apply():
        n = attach(album, MUSICBRAINZ, mds)
        apply_rules(album)
        _status('MusicBrainz: "%s" paired with %d of %d tracks%s'
                % (node.get('title'), n, len(album.tracks), '' if fitted else ' (no exact pressing fit)'))
        _refresh_panel()
    _when_loaded(album, apply)


# -- step 3: New Value from the per-field rule ------------------------------------------------------

# Release-level tags also shown on the album row.
_ALBUM_TAGS = ('album', 'albumartist', 'date', 'originaldate', 'originalyear', 'label', 'catalognumber',
               'barcode', 'releasetype', 'releasecountry', 'media', 'genre', 'script')


def apply_rules(album):
    """Set New Value from the per-field rule (rules.py) on every track that has both sources.
    Never touches a tag the user already picked a source for, nor tags no source has.
    Records the rule's choice in track.rule_sources[tag] (the user's picks live in value_sources)."""
    changed = 0
    album_values = {}
    for track in album.tracks:
        sources = getattr(track, 'source_metadata', None) or {}
        if len(sources) < 2:
            continue
        user = dict(getattr(track, 'value_sources', None) or {})
        for f in track.files:
            user.update(getattr(f, 'value_sources', None) or {})
        rule_sources = getattr(track, 'rule_sources', None) or {}
        tags = {t for md in sources.values() for t in md if not t.startswith('~')}
        for tag in sorted(tags):
            if tag in user:
                continue
            choice = choose(tag, {name: list(md.getall(tag)) for name, md in sources.items()})
            if choice is None:
                continue
            name, values = choice
            rule_sources[tag] = name
            if tag in _ALBUM_TAGS:
                album_values.setdefault(tag, values)
            if list(track.metadata.getall(tag)) == values:
                continue
            track.metadata[tag] = values
            track.orig_metadata[tag] = values
            for f in track.files:
                f.metadata[tag] = values
            changed += 1
        track.rule_sources = rule_sources
        for f in track.files:
            f.update()
        track.update()
    for tag, values in album_values.items():
        album.metadata[tag] = values
    if album_values:
        album.update(update_tracks=False)
    return changed


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
    api.register_track_metadata_processor(on_track_built)
    api.register_album_metadata_processor(on_mb_album)
    for name, doc in (('_ma_band_country', 'Band country from Metal Archives, e.g. "Italy".'),
                      ('_ma_band_country_code', 'Band country code from Metal Archives, e.g. "IT".'),
                      ('_ma_band_status', 'Band status from Metal Archives, e.g. "Active".'),
                      ('_ma_band_formed', 'Year the band formed, from Metal Archives.'),
                      ('_ma_album_id', 'Metal Archives album (pressing) id.'),
                      ('_ma_band_id', 'Metal Archives band id.')):
        api.register_script_variable(name, documentation=doc)

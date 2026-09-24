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
    OptionsPage,
    PluginApi,
)
from picard.util import thread

from .ma_client import (
    MAClient,
    MAError,
)
from .ma_release import (
    album_id_for,
    country_code,
    pressing_id_of,
    auto_pick,
    build_release,
    fits,
    format_kind,
    lineup_tags,
    ma_date,
    mb_genres,
    narrow_pressings,
    rank_hits,
    title_key,
    title_score,
    track_count_compatible,
)
from .discogs_client import DiscogsClient
from .discogs_release import (
    build_node,
    clean_name,
    flat_tracklist,
)
from .folder_parse import folder_hints
from . import pressings_panel
from .pressings import (
    candidate,
    judge,
    from_discogs,
    from_ma,
    from_mb,
)
from .rules import (
    POSITION_TAGS,
    PRESSING_TAGS,
    choose,
)
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
_originals = {}


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

MA_INFO_KEY = 'metallib_ma_info'


def _band_info(band_id, code=''):
    try:
        band = client().band(band_id) if band_id else {}
    except MAError:
        band = {}
    band['country_code'] = code or country_code(band.get('country'))
    return band


def _fetch_pressing(pressing_id):
    """Thread: everything a MetalArchivesAlbum needs, from one pressing id (for session restore)."""
    page = client().album(pressing_id)
    try:
        versions = client().versions(pressing_id)
    except MAError:
        versions = []
    version = next((v for v in versions if str(v['album_id']) == str(pressing_id)), None)
    original = min((ma_date(v['date']) for v in versions if ma_date(v['date'])), default='')
    info = {'album_id': page['album_id'], 'band_id': page['band_id'], 'cover_url': page['cover_url'],
            'band': _band_info(page['band_id']), 'lineup': page.get('lineup') or []}
    return {'node': build_release(page, version, original), 'info': info}


class MetalArchivesAlbum(Album):
    """An album built from Metal Archives data instead of a MusicBrainz release."""

    def __init__(self, album_id, release_node, ma_info):
        super().__init__(album_id)
        self._ma_node = release_node
        self.ma_info = ma_info          # {'album_id', 'band_id', 'url', 'cover_url'}
        if release_node is not None:
            # Kept inside the node, which a saved session stores: restoring rebuilds this album.
            release_node[MA_INFO_KEY] = ma_info

    def load(self, priority=False, refresh=False):
        if self._ma_node is None:
            # Restored from a session without its data: fetch the pressing again (MA cache first).
            self.loaded = False
            self.status = AlbumStatus.LOADING
            thread.run_task(partial(_fetch_pressing, pressing_id_of(self.id)), self._fetched)
            return
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

    def _fetched(self, result=None, error=None):
        if error or not result:
            self.error_append('Metal Archives: %s' % (error or 'pressing not found'))
            self.status = AlbumStatus.ERROR
            self.update()
            return
        self.ma_info = result['info']
        self._ma_node = result['node']
        self._ma_node[MA_INFO_KEY] = self.ma_info
        self.load()

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
    band['country_code'] = hit.get('band_country', '') or country_code(band.get('country'))
    return {'base': base, 'versions': versions, 'checked': checked, 'narrowed_by': why,
            'original_date': original, 'more': len(candidates) > max_fetches, 'band': band}


# ------------------------------------------------------------------------------------------------
# Main-thread flow
# ------------------------------------------------------------------------------------------------

def _local_info(cluster):
    files = list(cluster.iterfiles())
    first = files[0] if files else None
    hints = folder_hints(first.filename) if first else {}
    md = first.orig_metadata if first else {}
    return {
        'files': files,
        'band': cluster.metadata['albumartist'] or (md['albumartist'] or md['artist'] if first else '')
                or hints.get('artist', ''),
        'album': cluster.metadata['album'] or (md['album'] if first else '') or hints.get('album', ''),
        'folder': os.path.basename(os.path.dirname(first.filename)) if first else '',
        'catalog': (md['catalognumber'] if first else '') or hints.get('catalog', ''),
        'media': (md['media'] if first else '') or hints.get('media', ''),
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
        want = len(local['files'])
        rest = [c for c in result['checked'] if not c['fits']]
        ordered = fitting + sorted(rest, key=lambda c: len(c['page']['tracks']) != want)
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
    album = _build_album(cluster, local, hit, chosen, result['original_date'], result.get('band') or {})
    _record_ma(album, result, chosen['version']['album_id'])


def _record_ma(album, result, chosen_id):
    """Every pressing MA lists, for the Pressings panel (the ones already fetched with their fit)."""
    items = from_ma(result['versions'], result['checked'])
    for c in items:
        # The lookup judged fit against the tracklist it had (for an MB album: MB's lengths); the
        # lists judge against the files, so leave it to them (the pages are cached).
        c['fits'] = None
    extra = {'versions': result['versions'], 'original_date': result['original_date'],
             'band': result.get('band') or {}}
    _when_loaded(album, lambda: pressings_panel.record(album, METAL_ARCHIVES, items, chosen_id, extra))


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
    start_discogs(album)
    if page['cover_url']:
        thread.run_task(partial(client().fetch_bytes, page['cover_url']), partial(_on_cover, album))
    return album


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
    start_discogs(album)
    rg = (release_node.get('release-group') or {}).get('id')
    if rg:
        _when_loaded(album, partial(list_mb_pressings, album, rg, release_node['id'], _release_seconds(release_node)))


def _release_seconds(node):
    return [round((t.get('length') or 0) / 1000) for m in node.get('media') or [] for t in m.get('tracks') or []]


def list_mb_pressings(album, release_group_id, chosen_id, chosen_seconds=None):
    """Every release in the release group, for the Pressings panel."""
    _api.tagger.mb_api.browse_releases(partial(_on_mb_pressings, album, chosen_id, chosen_seconds),
                                       **{'release-group': release_group_id, 'limit': '100'})


def _on_mb_pressings(album, chosen_id, chosen_seconds, document=None, http=None, error=None):
    if error or not document or album.id not in _api.tagger.albums:
        return
    pressings_panel.record(album, MUSICBRAINZ, from_mb(document.get('releases') or []), chosen_id)
    if chosen_seconds is not None:
        # The shown release's fit against the files -- "no lengths" when MusicBrainz has no durations
        # for it (the MB column's Length row is then empty, user: A.N.I.M.A.L. 1994 US).
        files = pressings_panel.local_info(album)['lengths']
        pressings_panel.note(album, MUSICBRAINZ, chosen_id, len(chosen_seconds), judge(chosen_seconds, files))


def _background_ma(band, title, local):
    """Thread: find the MA album and pressing without asking anything."""
    pick = auto_pick(rank_hits(_search(band, title), band, title))
    if pick is None:
        return None
    result = _resolve(pick, local, max_fetches=BACKGROUND_PRESSING_FETCHES)
    fitting = [c for c in result['checked'] if c['fits']]
    # Nothing fits: a pressing with the album's track count, not the first one checked (a 10-track
    # vinyl was shown for an 11-track CD whose MA times all differ).
    same = [c for c in result['checked'] if len(c['page']['tracks']) == len(local['lengths'])]
    chosen = fitting[0] if fitting else (same or result['checked'] or [None])[0]
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
        if pressings_panel.blocked(album, METAL_ARCHIVES):
            return
        n = attach(album, METAL_ARCHIVES, mds)
        apply_rules(album)
        _status('Metal Archives: "%s" (%s) paired with %d of %d tracks'
                % (page['album'], version.get('format', ''), n, len(album.tracks)))
        _refresh_panel()
    _when_loaded(album, apply)
    _record_ma(album, result['result'], version['album_id'])


def _ma_fix(ma_album_id, band, lineup, md):
    MetalArchivesAlbum._strip_fake_ids(md)
    md['~ma_album_id'] = ma_album_id
    if band.get('genre'):
        md['genre'] = band['genre']
    for key in ('country', 'country_code', 'status', 'formed'):      # %_ma_band_country_code% etc.
        if band.get(key):
            md['~ma_band_' + key] = band[key]
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
        ok = bool(fits(node_lengths, album_lengths))

        def note_fit(document=document, node_lengths=node_lengths):
            files = pressings_panel.local_info(album)['lengths']       # the lists judge vs the files
            pressings_panel.note(album, MUSICBRAINZ, document['id'], len(node_lengths), judge(node_lengths, files))
        _when_loaded(album, note_fit)
        if ok:
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
        if pressings_panel.blocked(album, MUSICBRAINZ):
            return
        n = attach(album, MUSICBRAINZ, mds)
        apply_rules(album)
        _status('MusicBrainz: "%s" paired with %d of %d tracks%s'
                % (node.get('title'), n, len(album.tracks), '' if fitted else ' (no exact pressing fit)'))
        _refresh_panel()
        pressings_panel.record(album, MUSICBRAINZ, from_mb([node]), node['id'])
        rg = (node.get('release-group') or {}).get('id')
        if rg:
            list_mb_pressings(album, rg, node['id'], _release_seconds(node))
    _when_loaded(album, apply)


# -- step 3: New Value from the per-field rule ------------------------------------------------------

# Release-level tags also shown on the album row.
_ALBUM_TAGS = ('album', 'albumartist', 'date', 'originaldate', 'originalyear', 'label', 'catalognumber',
               'barcode', 'releasetype', 'releasecountry', 'media', 'genre', 'script')


# Hidden (script-only, never written) facts the naming script uses. The rules skip ~tags, so they
# are carried over here: the band's from Metal Archives, the pressing's from whichever source the
# catalog number comes from (so "(Digipak)" / "(Lim. Ed.)" describe the same pressing as "[SOM 650B]").
_PRESSING_FACTS = ('~releasecomment', '~releasepackaging')


def _hidden_facts(sources, user, rule_sources):
    out = {}
    ma = sources.get(METAL_ARCHIVES)
    if ma is not None:
        for tag in ma:
            if tag.startswith('~ma_band_'):
                out[tag] = list(ma.getall(tag))
    pressing = sources.get(user.get('catalognumber') or rule_sources.get('catalognumber')
                           or rule_sources.get('media') or '')
    if pressing is not None:
        for tag in _PRESSING_FACTS:
            out[tag] = list(pressing.getall(tag)) if tag in pressing else ['']
    return out


def _base_source(album):
    """The source the album itself was loaded from: New Value starts from its values."""
    return METAL_ARCHIVES if isinstance(album, MetalArchivesAlbum) else MUSICBRAINZ


def _keep_file_value(track, tag):
    """No source may give `tag`: New Value keeps what each file already has (user: "keep current
    info"); a file without it gets none -- except the date, which then becomes the first-release
    date (user)."""
    first = track.metadata['originaldate'] or track.metadata['originalyear']
    for md, orig in [(f.metadata, f.orig_metadata) for f in track.files] or [(track.metadata, None)]:
        if orig is not None and tag in orig and any(orig.getall(tag)):
            md[tag] = list(orig.getall(tag))
        elif tag == 'date' and first:
            md[tag] = first
        elif tag in md:
            del md[tag]
    if track.files:
        if tag in track.files[0].metadata:
            track.metadata[tag] = list(track.files[0].metadata.getall(tag))
        elif tag in track.metadata:
            del track.metadata[tag]


def apply_rules(album):
    """Set New Value from the per-field rule (rules.py) on every track that has both sources.
    Never touches a tag the user already picked a source for, nor tags no source has -- unless the
    album's own source is set to "Album info only" / "Don't use this source" in its pressing list:
    then its values that no other source replaces fall back to the files' own (_keep_file_value).
    Records the rule's choice in track.rule_sources[tag] (the user's picks live in value_sources)."""
    changed = 0
    album_values = {}
    base_mode = pressings_panel.mode(album, _base_source(album))
    for track in album.tracks:
        sources = getattr(track, 'source_metadata', None) or {}
        if len(sources) < 2 and base_mode is None:
            continue
        user = dict(getattr(track, 'value_sources', None) or {})
        for f in track.files:
            user.update(getattr(f, 'value_sources', None) or {})
        rule_sources = getattr(track, 'rule_sources', None) or {}
        tags = {t for md in sources.values() for t in md if not t.startswith('~')}
        if base_mode is not None:
            # the album's own values that the switched-off source put into New Value
            tags |= {t for t in track.metadata if not t.startswith('~')}
        # Performers from ONE source per track (MA first): MB and MA word instruments differently
        # ("electric guitar" / "guitar"), so mixing them doubled every credit (user review).
        perf = next((n for n in (METAL_ARCHIVES, MUSICBRAINZ, DISCOGS)
                     if n in sources and any(t.startswith('performer:') for t in sources[n])), None)
        for tag in sorted(tags):
            if tag in user or tag in POSITION_TAGS:
                continue
            if tag.startswith('performer:') and perf and tag not in sources[perf]:
                for md in [track.metadata] + [f.metadata for f in track.files]:
                    if tag in md:
                        del md[tag]
                continue
            choice = choose(tag, {name: list(md.getall(tag)) for name, md in sources.items()})
            if choice is None:
                if base_mode == pressings_panel.OFF or (base_mode == pressings_panel.ALBUM_ONLY
                                                        and tag in PRESSING_TAGS):
                    _keep_file_value(track, tag)
                    rule_sources.pop(tag, None)
                    changed += 1
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
        for tag, values in _hidden_facts(sources, user, rule_sources).items():
            track.metadata[tag] = values
            for f in track.files:
                f.metadata[tag] = values
            if tag.startswith('~ma_band_') or tag in _PRESSING_FACTS:
                album_values.setdefault(tag, values)
        for f in track.files:
            f.update()
        track.update()
    for tag, values in album_values.items():
        album.metadata[tag] = values
    if album_values:
        album.update(update_tracks=False)
    return changed


# -- Discogs: third source column -------------------------------------------------------------------

DISCOGS = 'Discogs'
DG_PRESSING_FETCHES = 4
_discogs = None


def discogs():
    global _discogs
    if _discogs is None:
        from picard.const.appdirs import plugin_folder
        folder = os.path.join(os.path.dirname(os.path.abspath(plugin_folder())), 'metallib')
        os.makedirs(folder, exist_ok=True)
        _discogs = DiscogsClient(os.path.join(folder, 'discogs_cache.sqlite'),
                                 lambda: _api.plugin_config['discogs_token'])
    return _discogs


def _split_dg_title(title):
    """Discogs search titles are "Artist - Title"."""
    band, _, album = (title or '').partition(' - ')
    return (clean_name(band), album) if album else ('', title or '')


def _overlap(release, titles):
    theirs = {title_key(t['title']) for t in flat_tracklist(release)}
    return sum(1 for t in titles if title_key(t) in theirs)


def _background_discogs(band, title, local):
    """Thread: master by title/artist, confirmed by track-title overlap; then the pressing."""
    client = discogs()
    best = None                                    # (overlap, release, master_id)
    for kind, hits in (('master', client.search_masters(band, title)), ('release', None)):
        if kind == 'release':
            if best:
                break
            hits = client.search_releases(band, title)
        ranked = []
        for h in hits or []:
            hb, ht = _split_dg_title(h.get('title'))
            score = title_score(ht, title)
            if score >= 0.8 and (not hb or title_score(hb, band) >= 0.8):
                ranked.append((score, h))
        ranked.sort(key=lambda sh: -sh[0])
        for _, h in ranked[:3]:
            if kind == 'master':
                m = client.master(h['id'])
                rel = client.release(m['main_release']) if m.get('main_release') else None
                mid = h['id']
            else:
                rel, mid = client.release(h['id']), None
            if rel:
                ov = _overlap(rel, local['titles'])
                if best is None or ov > best[0]:
                    best = (ov, rel, mid)
    if not best or best[0] < max(2, len(local['titles']) // 2):
        return None                                # not clearly the same album: no column rather than a wrong one
    _, release, master_id = best
    raw = client.versions(master_id) if master_id else []
    if raw:
        versions = [{'album_id': v.get('id'), 'catalog': v.get('catno', ''), 'format': v.get('format', ''),
                     'label': v.get('label', ''), 'country': v.get('country', '')}
                    for v in raw if v.get('id')]
        cands, _ = narrow_pressings(versions, local['folder'], local['catalog'], local['media'])
        for v in cands[:DG_PRESSING_FETCHES]:
            rel = release if v['album_id'] == release.get('id') else client.release(v['album_id'])
            # durations that fit; a release without any durations cannot confirm the pressing
            if judge([t['length'] for t in flat_tracklist(rel)], local['lengths']) is True:
                return {'release': rel, 'versions': raw}
    return {'release': release, 'versions': raw}


def _dg_release_candidate(release, lengths):
    """The release itself as a Pressings-panel entry (a release without a master has no versions list)."""
    labels = release.get('labels') or [{}]
    fmt = ', '.join(' '.join([f.get('name', '')] + list(f.get('descriptions') or []))
                    for f in release.get('formats') or [])
    secs = [t['length'] for t in flat_tracklist(release)]
    return candidate(DISCOGS, release['id'], release.get('released'), fmt, clean_name(labels[0].get('name', '')),
                     labels[0].get('catno'), release.get('country'), '', len(secs),
                     judge(secs, lengths))


def start_discogs(album):
    if not discogs().has_token():
        return
    def begin():
        files = list(album.iterfiles())
        local = {'titles': [t.metadata['title'] for t in album.tracks],
                 'lengths': [round((t.metadata.length or 0) / 1000) for t in album.tracks],
                 'folder': os.path.basename(os.path.dirname(files[0].filename)) if files else '',
                 'catalog': album.metadata['catalognumber'], 'media': album.metadata['media']}
        thread.run_task(partial(_background_discogs, album.metadata['albumartist'], album.metadata['album'], local),
                        partial(_on_discogs, album))
    _when_loaded(album, begin)


def _on_discogs(album, result=None, error=None):
    if error:
        _api.logger.warning("Discogs lookup failed: %s", error)
        return
    if not result:
        _status('Discogs: no release clearly matching "%s"' % album.metadata['album'])
        return
    versions, result = result['versions'], result['release']
    built = build_node(result)
    mds = track_metadata(built['node'], fix=partial(_dg_fix, built))
    lengths = pressings_panel.local_info(album)['lengths']
    items = from_discogs(versions) + [_dg_release_candidate(result, lengths)]
    if pressings_panel.blocked(album, DISCOGS):
        pressings_panel.record(album, DISCOGS, items)       # the list, but the user's row stays
        return
    n = attach(album, DISCOGS, mds)
    apply_rules(album)
    pressings_panel.record(album, DISCOGS, items, result['id'])
    _status('Discogs: "%s" (%s) paired with %d of %d tracks'
            % (result.get('title'), result.get('id'), n, len(album.tracks)))
    _refresh_panel()


def _dg_fix(built, md):
    for tag in [t for t in md if t.startswith('musicbrainz_')]:
        del md[tag]                                     # placeholders, never real MusicBrainz ids
    if built['genres']:
        md['genre'] = built['genres']
    try:
        credits = built['credits'][int(md['~absolutetracknumber'] or 0) - 1]
    except (ValueError, IndexError):
        credits = {}
    for tag, names in credits.items():
        md[tag] = names


class MetalLibOptionsPage(OptionsPage):
    NAME = 'metallib'
    TITLE = 'MetalLib'
    PARENT = 'plugins'

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QFormLayout(self)
        self.token = QtWidgets.QLineEdit(self)
        self.token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText('your personal access token from discogs.com/settings/developers')
        layout.addRow('Discogs token:', self.token)
        note = QtWidgets.QLabel('Needed for the Discogs column. It is stored in MetalLib\'s settings and only '
                                'ever sent to api.discogs.com. Leave empty to skip Discogs.')
        note.setWordWrap(True)
        layout.addRow(note)

    def load(self):
        self.token.setText(self.api.plugin_config['discogs_token'])

    def save(self):
        self.api.plugin_config['discogs_token'] = self.token.text().strip()


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


# -- folder names as hints for untagged files --------------------------------------------------------

def _album_artist_from_path(filename, album, artist):
    """Replaces Picard's clustering fallback for files WITHOUT album/artist tags: the folder parser
    understands scene names ("Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-GRP") and the library's
    "Artist (CC)/YYYY - Album [..]" layout. Used only to group files and name the cluster (which is
    what lookups search with) -- never written into a file's tags."""
    if album and artist:
        return album, artist
    try:
        hints = folder_hints(filename)
    except Exception:
        hints = {}
    if hints.get('album') and hints.get('confidence') != 'low':
        return album or hints['album'], artist or hints['artist']
    return _originals['album_artist_from_path'](filename, album, artist)


# -- session restore ------------------------------------------------------------------------------
# A saved session stores each album's id and release data. Picard rebuilds every album as a
# MusicBrainz album, which for "metallib-ma-..." ids asks MusicBrainz for a release that does not
# exist. These hooks rebuild them as Metal Archives albums instead.

def restore_album(album_id, node=None):
    tagger = _api.tagger
    album = tagger.albums.get(album_id)
    if isinstance(album, MetalArchivesAlbum):
        return album
    info = (node or {}).get(MA_INFO_KEY)
    album = MetalArchivesAlbum(album_id, node if info else None, info or {})
    tagger.albums[album_id] = album
    tagger.album_added.emit(album)
    album.load()
    _when_loaded(album, partial(_after_restore, album))
    return album


def _after_restore(album):
    """The other columns, the pressing lists and the cover come back too (MA pages from the cache)."""
    info = album.ma_info or {}
    start_mb_lookup(album, album.metadata['albumartist'], album.metadata['album'])
    start_discogs(album)
    pid = pressing_id_of(album.id)
    thread.run_task(partial(_versions_of, pid), partial(_restored_versions, album, pid))
    if info.get('cover_url'):
        thread.run_task(partial(client().fetch_bytes, info['cover_url']), partial(_on_cover, album))


def _versions_of(pressing_id):
    try:
        return client().versions(pressing_id)
    except MAError:
        return []


def _restored_versions(album, pressing_id, result=None, error=None):
    if error or not result or album.id not in _api.tagger.albums:
        return
    original = min((ma_date(v['date']) for v in result if ma_date(v['date'])), default='')
    pressings_panel.record(album, METAL_ARCHIVES, from_ma(result), pressing_id,
                           {'versions': result, 'original_date': original,
                            'band': (album.ma_info or {}).get('band') or {}})


def _session_strategy(self, album_id, cached_node):
    if pressing_id_of(album_id) is None:
        return _originals['session_strategy'](self, album_id, cached_node)
    # Picard's "no MusicBrainz requests on load" does not apply: an MA album needs no MusicBrainz,
    # and without saved data its pressing comes from MetalLib's MA cache (the network only if not cached).
    album = restore_album(album_id, cached_node)
    self._ui_state.ensure_album_visible(album, self._saved_expanded_albums)
    return album


def _session_build(self, album_id, node):
    if pressing_id_of(album_id) is None:
        return _originals['session_build'](self, album_id, node)
    return restore_album(album_id, node)


def _tagger_load_album(album_id, *args, **kwargs):
    if pressing_id_of(album_id) is not None:
        return restore_album(album_id)
    return _originals['load_album'](album_id, *args, **kwargs)


def _hook_session(api):
    from picard.session import session_loader
    manager = session_loader.AlbumManager
    _originals['session_strategy'] = manager.load_album_with_strategy
    _originals['session_build'] = manager._build_from_cache
    manager.load_album_with_strategy = _session_strategy
    manager._build_from_cache = _session_build
    _originals['load_album'] = api.tagger.load_album
    api.tagger.load_album = _tagger_load_album


def _unhook_session():
    from picard.session import session_loader
    manager = session_loader.AlbumManager
    if 'session_strategy' in _originals:
        manager.load_album_with_strategy = _originals.pop('session_strategy')
        manager._build_from_cache = _originals.pop('session_build')
    if 'load_album' in _originals and _api is not None:
        try:
            del _api.tagger.load_album          # back to the class method
        except AttributeError:
            pass
        _originals.pop('load_album')


def disable() -> None:
    _unhook_session()
    pressings_panel.uninstall()
    from picard import cluster as picard_cluster
    if 'album_artist_from_path' in _originals:
        picard_cluster.album_artist_from_path = _originals['album_artist_from_path']


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    api.register_cluster_action(LoadFromMetalArchives)
    from picard import cluster as picard_cluster
    _originals['album_artist_from_path'] = picard_cluster.album_artist_from_path
    picard_cluster.album_artist_from_path = _album_artist_from_path
    api.plugin_config.register_option('discogs_token', '')
    api.plugin_config.register_option(pressings_panel.LAYOUT_OPTION, '')
    api.register_options_page(MetalLibOptionsPage)
    api.register_track_metadata_processor(on_track_built)
    api.register_album_metadata_processor(on_mb_album)
    pressings_panel.install(api)
    _hook_session(api)
    for name, doc in (('_ma_band_country', 'Band country from Metal Archives, e.g. "Italy".'),
                      ('_ma_band_country_code', 'Band country code from Metal Archives, e.g. "IT".'),
                      ('_ma_band_status', 'Band status from Metal Archives, e.g. "Active".'),
                      ('_ma_band_formed', 'Year the band formed, from Metal Archives.'),
                      ('_ma_album_id', 'Metal Archives album (pressing) id.'),
                      ('_ma_band_id', 'Metal Archives band id.')):
        api.register_script_variable(name, documentation=doc)

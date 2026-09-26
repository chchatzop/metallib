# MetalLib tracks -- place files on album tracks by TITLE + DURATION, never by filename.
#
#   * Picard no longer guesses a TRACK NUMBER from the filename (a title guess is still allowed:
#     it is a title, not a position).
#   * When files are matched to a loaded album automatically (dropped on the album, album
#     loaded after a lookup), each file is placed by title + duration, one file per track.
#     Undecidable files are left in the album's unmatched files and flagged; a confident match
#     that disagrees with the file's track-number tag is placed anyway and flagged "renumbered".
#     Dropping files onto a specific TRACK is a manual placement and is left alone.
#   * %_placement%          '' (fine), "renumbered" or "unplaced" -- use it in a custom column
#     %_placement_reason%   why, in words
#   * Right-click an album -> "Track placement report..." lists every flagged file.
#   * An album that was NOT looked up (a cluster): an offset track numbering (02..09, nothing
#     missing) becomes 01..08 in New Value, flagged "renumbered"; gaps, duplicates or missing
#     numbers are left alone and flagged "check numbering" (numbering.py).
#   * Wrong-release guard: when a looked-up album finishes loading and under a third of its files
#     fit its tracklist while the artist or track count contradicts it, the files go back to
#     clustering and the album is dropped (flag "unplaced", reason "wrong release ..."). Under
#     half fitting -> the album row is flagged "suspect release" instead.
#
# Picard 3.0 has no plugin hook for the file->track assignment, so this wraps
# Album.match_files and File._guess_tracknumber_and_title; disable() restores both.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial
import os
import re

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.plugin3.api import (
    Album,
    BaseAction,
    File,
    PluginApi,
)
from picard.similarity import similarity2

from .placement import (
    ASSUMED,
    RENUMBERED,
    UNPLACED,
    place,
)
from .numbering import (
    number,
    plan,
)
from .release_check import (
    SUSPECT,
    WRONG,
    check_release,
)


_ATTR = '_metallib_placement'       # (status, reason) stored on the File object
NUMBERING = 'check numbering'       # a cluster whose track numbers need a look
_FLAGS = (RENUMBERED, UNPLACED, ASSUMED, NUMBERING)
_api = None
_originals = {}
_resolving = False


def _set_flag(file, status, reason):
    setattr(file, _ATTR, (status, reason))
    for md in (file.metadata, file.orig_metadata):
        md['~placement'] = status if status in _FLAGS else ''
        md['~placement_reason'] = reason if status in _FLAGS else ''
    # Column text is only recomputed when the row updates; a file that did not move (already in
    # "Unmatched Files") would otherwise keep showing its old, empty value.
    file.update_item(update_selection=False)


# "... - 01 - Title", "01. Title", "01 - Title", "1-01 Title": the text after the LAST track-number
# token. Only the title is taken from it -- the number itself is never used.
_TITLE_AFTER_NUMBER_RE = re.compile(r'(?:^|[\s._-])(?:\d{1,2}-)?\d{1,3}\s*[-._)]?\s+(?=\S)(?!.*\s\d{1,3}\s*[-._)]\s)(.+)$')


# The library's own layout "Band - Album - 04 - Title" / "... - 2-04 - Title": the FIRST such track
# token decides, so a title with its own " 2 - " ("Part 2 - The End") stays whole (audit part 2 L3).
_LIBRARY_TRACK_RE = re.compile(r' - (?:\d{1,2}-)?\d{1,3} - (.+)$')


def title_from_filename(name):
    stem = os.path.splitext(name)[0] if re.search(r'\.[A-Za-z0-9]{2,4}$', name) else name
    m = _LIBRARY_TRACK_RE.search(stem) or _TITLE_AFTER_NUMBER_RE.search(stem)
    title = m.group(1) if m else _originals['tracknum_and_title_from_filename'](stem).title or ''
    return title.strip(' -._')


def _guess_title_only(self, metadata):
    # Replaces File._guess_tracknumber_and_title: keep a title guess, never a track number.
    if 'title' not in metadata:
        title = title_from_filename(self.base_filename)
        if title:
            metadata['title'] = title


def _track_info(track):
    md = track.metadata
    number = md['tracknumber']
    disc, discs = md['discnumber'], md['totaldiscs']
    label = '%s-%s' % (disc, number.zfill(2)) if disc and discs not in ('', '1') else number
    return {'title': md['title'], 'length': md.length or 0, 'number': number, 'label': label,
            'recording_ids': _recording_ids_of_track(track)}


def _file_info(file):
    md = file.orig_metadata
    # Recordings the file's AUDIO matched: our fingerprint action, or Picard's own Scan
    # (match_recordingid). Never the file's musicbrainz_recordingid tag: tags can lie.
    fp = set(getattr(file, _FP_ATTR, None) or ())
    if getattr(file, 'match_recordingid', None):
        fp.add(file.match_recordingid)
    return {'title': md['title'], 'length': md.length or 0, 'tracknumber': md['tracknumber'],
            'recording_ids': fp}


def resolve(album, files):
    """Place `files` on `album`'s tracks by title + duration (see placement.place)."""
    global _resolving
    files = [f for f in files if f.state != File.State.REMOVED]
    if not files:
        return
    tracks = list(album.tracks)
    results = place([_file_info(f) for f in files], [_track_info(t) for t in tracks], similarity2)
    moving = set(files)
    _resolving = True
    try:
        for f, r in zip(files, results):
            status, reason = r['status'], r['reason']
            target = album.unmatched_files
            if r['track'] is not None:
                track = tracks[r['track']]
                others = [o for o in track.files if o not in moving]
                if others:
                    status, reason = UNPLACED, 'track %s already holds %s' % (
                        track.metadata['tracknumber'], os.path.basename(others[0].filename))
                else:
                    target = track
            if f.parent_item is not target:
                f.move(target)
            _set_flag(f, status, reason)
            _api.logger.debug("placement %s (%s): %s", status, reason, f.filename)
    finally:
        _resolving = False


def _match_files(self, files):
    files = list(files)
    if not self.loaded:
        return _originals['match_files'](self, files)
    with self.tagger.window.metadata_box.ignore_updates:
        resolve(self, files)
    # The first match after the album finished loading is the lookup's; judge the release once.
    # Later calls (files dragged onto the album by hand) are the user's decision.
    if not getattr(self, '_metallib_checked', False):
        self._metallib_checked = True
        try:
            check_album(self)
        except Exception:
            _api.logger.exception("wrong-release check failed for %r", self)    # never break loading


def check_album(album):
    """Wrong-release guard: see release_check.check_release."""
    files = [f for f in album.iterfiles() if f.state != File.State.REMOVED]
    placed = [f for t in album.tracks for f in t.files]
    artists = [f.orig_metadata['albumartist'] or f.orig_metadata['artist'] for f in files]
    verdict, reason = check_release(len(files), len(placed), len(album.tracks), artists,
                                    album.metadata['albumartist'], similarity2)
    label = '"%s" by %s' % (album.metadata['album'], album.metadata['albumartist'])
    if verdict == WRONG:
        _api.logger.info("wrong release %s: %s", label, reason)
        # Not from inside the album's own load: let it finish, then take it apart.
        QtCore.QTimer.singleShot(0, lambda: _send_back(album, files, 'wrong release %s: %s' % (label, reason)))
    elif verdict == SUSPECT:
        album.metadata['~placement'] = 'suspect release'
        album.metadata['~placement_reason'] = reason
        album.update(update_tracks=False)


def _send_back(album, files, reason):
    tagger = album.tagger
    if album.id not in tagger.albums:
        return                                  # removed by the user meanwhile
    files = [f for f in files if f.state != File.State.REMOVED]
    for f in files:
        f.move(tagger.unclustered_files)
        _set_flag(f, UNPLACED, reason)
    tagger.remove_album(album)

    def clustered():
        # Try Metal Archives for them (the MetalLib MA plugin; once per set of files, so no loop).
        fallback = getattr(tagger, 'metallib_fallback_lookup', None)
        if fallback is not None:
            for cluster in {f.parent_item for f in files if f.parent_item in tagger.clusters}:
                fallback(cluster, reason)
    tagger.cluster(files, callback=clustered)
    tagger.window.set_statusbar_message("MetalLib: %s -- files sent back to clustering", reason)


def on_file_added_to_track(api, track, file):
    # A file put on a track by hand (not by resolve) is the user's decision: drop any old flag.
    if not _resolving and getattr(file, _ATTR, None):
        _set_flag(file, '', '')


# -- AcoustID: place unplaced files by their audio fingerprint -----------------------------------

_FP_ATTR = '_metallib_fp_recordings'


def _recording_ids_of_track(track):
    ids = {track.metadata['musicbrainz_recordingid']}
    mb = (getattr(track, 'source_metadata', None) or {}).get('MusicBrainz')
    if mb is not None:                              # MA album: ids from the paired MB column
        ids.add(mb['musicbrainz_recordingid'])
    return {i for i in ids if i}


class PlaceByFingerprint(BaseAction):
    TITLE = "Place by fingerprint (AcoustID)"

    def callback(self, objs):
        for album in objs:
            if not isinstance(album, Album) or not album.loaded:
                continue
            files = list(album.unmatched_files.iterfiles())
            if not files:
                self.tagger.window.set_statusbar_message('MetalLib: every file of "%s" is already placed',
                                                         album.metadata['album'])
                continue
            if not any(_recording_ids_of_track(t) for t in album.tracks):
                self.tagger.window.set_statusbar_message(
                    'MetalLib: "%s" has no MusicBrainz recording ids to compare fingerprints with',
                    album.metadata['album'])
                continue
            from picard.acoustid import find_fpcalc
            if not find_fpcalc():
                self.tagger.window.set_statusbar_message(
                    'MetalLib: fpcalc (Chromaprint) not found -- set it in Options → Fingerprinting')
                continue
            run = {'pending': set(files), 'done': False}
            self.tagger.window.set_statusbar_message('MetalLib: fingerprinting %d file(s) of "%s"...',
                                                     len(files), album.metadata['album'])
            for f in files:
                self.tagger._acoustid.analyze(f, partial(_fingerprinted, album, f, run))
            # Picard's AcoustID code never calls back for a file fpcalc cannot read (audit part 2 M3):
            # don't wait for it forever -- place with the fingerprints that did arrive.
            QtCore.QTimer.singleShot(FP_TIMEOUT_MS, partial(_fp_resolve, album, run))


FP_TIMEOUT_MS = 120000


def _fingerprinted(album, file, run, result=None, http=None, error=None):
    recordings = (result or {}).get('recordings') or []
    setattr(file, _FP_ATTR, {r.get('id') for r in recordings if r.get('id')})
    run['pending'].discard(file)
    if not run['pending']:
        _fp_resolve(album, run)


def _fp_resolve(album, run):
    if run['done'] or album.id not in _api.tagger.albums:
        return
    run['done'] = True
    missing = len(run['pending'])
    files = [f for f in album.unmatched_files.iterfiles()]
    before = len(files)
    with _api.tagger.window.metadata_box.ignore_updates:
        resolve(album, files)
    placed = before - len(list(album.unmatched_files.iterfiles()))
    _api.tagger.window.set_statusbar_message(
        'MetalLib: fingerprints placed %d of %d file(s) of "%s"%s', placed, before, album.metadata['album'],
        ' (%d could not be fingerprinted)' % missing if missing else '')


class PlacementReport(BaseAction):
    TITLE = "Track placement report..."

    def callback(self, objs):
        parts = []
        for album in objs:
            if not isinstance(album, Album):
                continue
            lines = []
            for f in album.iterfiles():
                status, reason = getattr(f, _ATTR, ('', ''))
                if status in (RENUMBERED, UNPLACED, ASSUMED):
                    lines.append("&nbsp;&nbsp;<b>%s</b> %s — %s" % (
                        status, _html(os.path.basename(f.filename)), _html(reason)))
            head = "<b>%s</b>" % _html(album.metadata['album'])
            if album.metadata['~placement']:
                head += " — <b>%s</b>: %s" % (_html(album.metadata['~placement']),
                                              _html(album.metadata['~placement_reason']))
            parts.append(head + ("<br>" + "<br>".join(lines) if lines else ": every file placed by title + duration"))
        QtWidgets.QMessageBox.information(
            self.tagger.window, "Track placement", "<br><br>".join(parts) or "Select an album.")


def _html(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# -- albums that were not looked up: track numbers ----------------------------------------------

def renumber_cluster(cluster):
    """New Value track numbers for a cluster's files: an offset run is shifted to 01..N, anything
    else odd is flagged. Only New Value changes; nothing is written until the user saves."""
    files = list(cluster.files)
    items = [{'disc': number(f.orig_metadata['discnumber']) or 1,
              'number': number(f.orig_metadata['tracknumber']),
              'total': number(f.orig_metadata['totaltracks'] or f.orig_metadata['tracktotal'])} for f in files]
    new, notes = plan(items)
    for i, f in enumerate(files):
        kind, text = notes.get(items[i]['disc'], ('', ''))
        if i in new:
            f.metadata['tracknumber'] = str(new[i])
            _set_flag(f, RENUMBERED, text)
        elif kind == 'problem':
            _set_flag(f, NUMBERING, text)
        elif getattr(f, _ATTR, ('', ''))[0] in (RENUMBERED, NUMBERING):
            if f.metadata['tracknumber'] != f.orig_metadata['tracknumber']:
                f.metadata['tracknumber'] = f.orig_metadata['tracknumber']   # an earlier shift, undone
            _set_flag(f, '', '')
        f.update()


def _cluster_add_files(self, files, new_album=True):
    _originals['cluster_add_files'](self, files, new_album=new_album)
    if not self.special and self.album is None:          # a real cluster, not an album's unmatched files
        renumber_cluster(self)


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    import picard.file as picard_file
    _originals['match_files'] = Album.match_files
    _originals['guess'] = File._guess_tracknumber_and_title
    _originals['tracknum_and_title_from_filename'] = picard_file.tracknum_and_title_from_filename
    Album.match_files = _match_files
    File._guess_tracknumber_and_title = _guess_title_only
    from picard.cluster import Cluster
    _originals['cluster_add_files'] = Cluster.add_files
    Cluster.add_files = _cluster_add_files
    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_album_action(PlacementReport)
    api.register_album_action(PlaceByFingerprint)
    api.register_script_variable('_placement', documentation='"renumbered", "unplaced" or empty.')
    api.register_script_variable('_placement_reason', documentation='Why a file was flagged.')
    # the current file name next to the title (user): a custom column, added once
    from . import file_column
    api.plugin_config.register_option(file_column.OPTION, False)
    file_column.install(api)


def disable() -> None:
    if 'match_files' in _originals:
        Album.match_files = _originals['match_files']
    if 'guess' in _originals:
        File._guess_tracknumber_and_title = _originals['guess']
    if 'cluster_add_files' in _originals:
        from picard.cluster import Cluster
        Cluster.add_files = _originals.pop('cluster_add_files')

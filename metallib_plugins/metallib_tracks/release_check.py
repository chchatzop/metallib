# MetalLib release check -- is the release a lookup attached these files to plausibly THEIR release?
#
# Picard's lookup takes the closest MusicBrainz hit even when the band is not on MusicBrainz at
# all: Aghar "Cellar of the Castle" landed on Mortlach "Relics of the Castle", Alltid Allena
# "Grey Metal" on Tapani Rinne "Grey". The title + duration placement already keeps every file off
# those tracks; this decides what to do with the album itself.
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re
import unicodedata


OK = 'ok'
SUSPECT = 'suspect'     # keep the album, flag it
WRONG = 'wrong'         # send the files back to clusters, drop the album

_TRANSLIT = str.maketrans({'ø': 'o', 'æ': 'ae', 'œ': 'oe', 'ß': 'ss', 'đ': 'd', 'ł': 'l',
                           'þ': 'th', 'ð': 'd', 'ı': 'i'})
_VARIOUS = {'variousartists', 'various', 'va'}
_SPLIT_RE = re.compile(r'\s+(?:/|&|and|x|vs\.?|with|feat\.?|ft\.?)\s+|\s*/\s*|,\s+', re.IGNORECASE)


def artist_key(name):
    """Canonical artist key (port of full_program tag_reader.artist_key): folds case, diacritics,
    a leading "The", "&" and all punctuation/spacing. Motörhead = Motorhead, AC/DC = ACDC."""
    s = (name or '').strip().lower()
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).translate(_TRANSLIT)
    stripped = re.sub(r'^the\s+', '', s)
    if stripped.strip() and stripped.strip() != 'the':
        s = stripped
    if re.search(r'[a-z0-9]', s):
        s = s.replace('&', ' and ')
    key = re.sub(r'[^a-z0-9]+', '', s)
    if key:
        return key
    uni = re.sub(r'[^\w]+', '', s, flags=re.UNICODE)
    return 'u:' + uni if uni else ''


def _parts(name):
    return [p for p in (artist_key(x) for x in _SPLIT_RE.split(name or '')) if p]


def same_artist(a, b, similarity):
    """True when two artist credits name the same act, allowing splits ("A / B"), collaborations
    and small spelling differences. Unknown on either side counts as the same (nothing to judge)."""
    ka, kb = artist_key(a), artist_key(b)
    if not ka or not kb or ka == kb:
        return True
    pa, pb = set(_parts(a)) | {ka}, set(_parts(b)) | {kb}
    if pa & pb:
        return True
    return similarity(a, b) >= 0.85


def track_count_compatible(local_count, release_count):
    """Port of full_program _mb_track_count_compatible: the smaller count must be >= 70% of the
    larger, so a bonus/deluxe edition still fits (11 vs 13) but an EP vs an album does not."""
    a, b = int(local_count or 0), int(release_count or 0)
    if not a or not b:
        return True
    return min(a, b) >= 0.7 * max(a, b)


def check_release(n_files, n_placed, n_tracks, file_artists, release_artist, similarity):
    """-> (verdict, reason).

    WRONG only when the title + duration placement agrees: almost nothing fits (under a third of
    the files) AND the artist or the track count contradicts the release. A release whose files
    DO fit is kept even under a different artist spelling (MusicBrainz often credits a band in
    another script). SUSPECT: fewer than half fit and one of the two contradicts.
    """
    if not n_files:
        return OK, ''
    known = [a for a in file_artists if artist_key(a)]
    artist_bad = False
    if known and artist_key(release_artist) not in _VARIOUS:
        top = max(set(known), key=known.count)
        artist_bad = not same_artist(top, release_artist, similarity)
    count_bad = not track_count_compatible(n_files, n_tracks)

    problems = []
    if artist_bad:
        problems.append('the files are by "%s", the release by "%s"' % (top, release_artist))
    if count_bad:
        problems.append('%d files vs %d tracks' % (n_files, n_tracks))
    fit = '%d of %d files fit its tracklist' % (n_placed, n_files)
    if not problems:
        return OK, ''
    if n_placed < n_files / 3:
        return WRONG, '%s; %s' % (fit, '; '.join(problems))
    if n_placed < n_files / 2:
        return SUSPECT, '%s; %s' % (fit, '; '.join(problems))
    return OK, ''

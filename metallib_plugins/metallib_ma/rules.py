# MetalLib -- which source's value New Value starts from, per tag (user: "per-field rule",
# configurable later). The user can still take any other source's value by double-clicking it.
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re


MB = 'MusicBrainz'
MA = 'Metal Archives'
DG = 'Discogs'          # third column: only fills what neither MA nor MB has

# Tags where Metal Archives is the better authority for metal (the user's list, plus the lineup
# credits MA carries and MusicBrainz rarely has for underground releases).
MA_FIRST = {'title', 'album', 'releasetype', 'genre', 'label', 'catalognumber', 'media',
            'lyricist', 'composer', 'writer', 'producer', 'engineer', 'mixer'}
MA_FIRST_PREFIXES = ('performer:',)

# Tags only MusicBrainz really has, or where it is the authority.
MB_FIRST = {'barcode', 'isrc', 'asin', 'releasecountry', 'script', 'language'}
MB_FIRST_PREFIXES = ('musicbrainz_',)

# Dates: the more precise value wins (MB "1996-03-12" over MA "1996"); equal precision -> MA,
# whose date belongs to the pressing the user picked.
DATE_TAGS = {'date', 'releasedate'}

# First release: Metal Archives' whenever it has one (user; MA's is the earliest of all its
# versions). Else the EARLIEST year the others know (a reissue's date must never become the
# original -- it names the folder "YYYY - Album"), the most precise value within that year; Discogs
# only when MB has none: its dates belong to a pressing, not the first release.
ORIGINAL_TAGS = {'originaldate', 'originalyear'}

# A track's POSITION belongs to the album the files are matched to, never to another source's
# column: a 10-track vinyl in the MA column renumbered an 11-track CD (06 twice, up to 10).
POSITION_TAGS = {'tracknumber', 'totaltracks', 'discnumber', 'totaldiscs'}

# What belongs to ONE physical release rather than to the album (user: "Album info only (no
# pressing)" in a source's list drops these from that source's column).
PRESSING_TAGS = {'date', 'releasedate', 'label', 'catalognumber', 'barcode', 'asin', 'media',
                 'releasecountry', 'releasestatus', 'musicbrainz_albumid', 'musicbrainz_discid',
                 # Picard's name for the RELEASE-track id is musicbrainz_trackid (written to FLAC as
                 # MUSICBRAINZ_RELEASETRACKID); the recording id is musicbrainz_recordingid (album-level).
                 'musicbrainz_trackid', 'musicbrainz_releasetrackid',
                 '~releasecomment', '~releasepackaging'}

DEFAULT_ORDER = (MA, MB, DG)    # everything else: MA if it has a value, else MB, else Discogs


def order_for(tag):
    """Sources to try for `tag`, most preferred first."""
    if tag in MB_FIRST or tag.startswith(MB_FIRST_PREFIXES):
        return (MB, MA, DG)
    if tag in MA_FIRST or tag.startswith(MA_FIRST_PREFIXES):
        return (MA, MB, DG)
    return DEFAULT_ORDER


def _precision(value):
    return len(re.findall(r'\d+', value or ''))        # "1996" 1, "1996-03" 2, "1996-03-12" 3


def _year(value):
    m = re.match(r'\s*(\d{4})', value or '')
    return int(m.group(1)) if m else 0


def choose(tag, source_values):
    """source_values: {source name: [values]} (a source may be missing or empty).
    -> (source name, values) for New Value, or None when no source has the tag."""
    if tag in POSITION_TAGS:
        return None
    have = {name: vals for name, vals in source_values.items() if vals and any(v for v in vals)}
    if not have:
        return None
    if tag in ORIGINAL_TAGS:
        if MA in have and _year(have[MA][0]):
            return MA, have[MA]
        known = ({n: v for n, v in have.items() if n != DG and _year(v[0])}
                 or {n: v for n, v in have.items() if _year(v[0])})
        if known:
            best = min(known, key=lambda n: (_year(known[n][0]), -_precision(known[n][0]), n != MA))
            return best, known[best]
    if tag in DATE_TAGS:
        best = max(have, key=lambda name: (_precision(have[name][0]), name == MA, name == MB))
        return best, have[best]
    for name in order_for(tag):
        if name in have:
            return name, have[name]
    name = next(iter(have))
    return name, have[name]

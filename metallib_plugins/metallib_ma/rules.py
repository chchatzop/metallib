# MetalLib -- which source's value New Value starts from, per tag (user: "per-field rule",
# configurable later). The user can still take any other source's value by double-clicking it.
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re


MB = 'MusicBrainz'
MA = 'Metal Archives'

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
DATE_TAGS = {'date', 'originaldate', 'releasedate'}

DEFAULT_ORDER = (MA, MB)        # everything else: MA if it has a value, else MB


def order_for(tag):
    """Sources to try for `tag`, most preferred first."""
    if tag in MB_FIRST or tag.startswith(MB_FIRST_PREFIXES):
        return (MB, MA)
    if tag in MA_FIRST or tag.startswith(MA_FIRST_PREFIXES):
        return (MA, MB)
    return DEFAULT_ORDER


def _precision(value):
    return len(re.findall(r'\d+', value or ''))        # "1996" 1, "1996-03" 2, "1996-03-12" 3


def choose(tag, source_values):
    """source_values: {source name: [values]} (a source may be missing or empty).
    -> (source name, values) for New Value, or None when no source has the tag."""
    have = {name: vals for name, vals in source_values.items() if vals and any(v for v in vals)}
    if not have:
        return None
    if tag in DATE_TAGS:
        best = max(have, key=lambda name: (_precision(have[name][0]), name == MA))
        return best, have[best]
    for name in order_for(tag):
        if name in have:
            return name, have[name]
    name = next(iter(have))
    return name, have[name]

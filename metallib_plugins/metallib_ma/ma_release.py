# MetalLib -- turn Metal Archives data into a Picard release, and pick the right pressing.
#
# Pure logic, no Picard or network imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re
import unicodedata


_MONTHS = {m: i for i, m in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
     'october', 'november', 'december'), 1)}


def ma_date(text):
    """"October 18th, 2019" -> "2019-10-18"; "March 2019" -> "2019-03"; "2019" -> "2019"; else ''."""
    t = (text or '').strip().lower()
    y = re.search(r'\b(1[89]\d{2}|20\d{2})\b', t)
    if not y:
        return ''
    month = next((n for name, n in _MONTHS.items() if name in t), 0)
    day = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)\b', t)
    if month and day:
        return '%s-%02d-%02d' % (y.group(1), month, int(day.group(1)))
    if month:
        return '%s-%02d' % (y.group(1), month)
    return y.group(1)


# -- media -----------------------------------------------------------------------------------------

DIGITAL, CD, VINYL, TAPE = 'Digital', 'CD', 'Vinyl', 'Cassette'


def format_kind(ma_format):
    """MA's format text ("2 12\" vinyls (45 RPM)", "Digital", "CD", "Cassette") -> media kind."""
    f = (ma_format or '').lower()
    if 'digital' in f:
        return DIGITAL
    if 'vinyl' in f or re.search(r'\b(7|10|12)"', f) or re.search(r'\blp\b', f):
        return VINYL
    if 'cassette' in f or 'tape' in f:
        return TAPE
    if 'cd' in f or 'sacd' in f:
        return CD
    return ''


# Folder-name tokens of the user's library: "[WEB]", "[SOM 532 LP]", "[ORM005 CD]", "(Vinyl)".
_HINTS = ((DIGITAL, r'\b(web|digital|bandcamp|flac web)\b'), (VINYL, r'\b(lp|vinyl|12"|7"|10")'),
          (TAPE, r'\b(tape|cassette|mc)\b'), (CD, r'\b(cd|cdr|cd-r|sacd)\b'))


def media_hint(folder_name, media_tag=''):
    """Media the local copy came from: the file's MEDIA tag first, then folder-name tokens."""
    k = format_kind(media_tag)
    if k:
        return k
    low = (folder_name or '').lower()
    for kind, pattern in _HINTS:
        if re.search(pattern, low):
            return kind
    return ''


def _norm_cat(s):
    s = unicodedata.normalize('NFKD', s or '').lower()
    return re.sub(r'[^a-z0-9]+', '', s)


def catalog_matches(catalog, *texts):
    """True when the pressing's catalog number appears in a folder name / catalognumber tag,
    ignoring spacing and punctuation ("SOM 532LP" ~ "[SOM 532 LP]")."""
    c = _norm_cat(catalog)
    if len(c) < 3:
        return False
    return any(c in _norm_cat(t) for t in texts if t)


# -- fit ----------------------------------------------------------------------------------------

DUR_TOL_S = 5


def tracklist_fit(track_lengths, file_lengths):
    """(count_ok, matched): does a tracklist fit the local files? matched = files whose length is
    within DUR_TOL_S of a distinct track (one-to-one, nearest first). Unknown lengths are skipped."""
    count_ok = len(track_lengths) == len(file_lengths)
    free = sorted(t for t in track_lengths if t)
    matched = 0
    for f in sorted(x for x in file_lengths if x):
        best = min(free, key=lambda t: abs(t - f), default=None)
        if best is not None and abs(best - f) <= DUR_TOL_S:
            free.remove(best)
            matched += 1
    return count_ok, matched


def fits(track_lengths, file_lengths):
    count_ok, matched = tracklist_fit(track_lengths, file_lengths)
    known = sum(1 for x in file_lengths if x)
    if not count_ok:
        return False
    # Lengths unknown on either side: the count is all there is to go by.
    if not known or not any(track_lengths):
        return True
    return matched >= 0.8 * known


def narrow_pressings(versions, folder_name, catalog_tag, media_tag):
    """Pressings worth fetching, most specific evidence first: a catalog number named in the
    folder/tag wins outright; else the ones on the same media; else all."""
    by_cat = [v for v in versions if catalog_matches(v.get('catalog'), folder_name, catalog_tag)]
    if by_cat:
        return by_cat, 'catalog number'
    hint = media_hint(folder_name, media_tag)
    if hint:
        same = [v for v in versions if format_kind(v.get('format')) == hint]
        if same:
            return same, 'media (%s)' % hint
    return list(versions), ''


# -- release node -------------------------------------------------------------------------------

_TYPES = {  # MA type -> (MusicBrainz primary type, secondary types)
    'full-length': ('Album', []), 'ep': ('EP', []), 'single': ('Single', []),
    'demo': ('Other', ['Demo']), 'live album': ('Album', ['Live']),
    'compilation': ('Album', ['Compilation']), 'split': ('Other', []),
    'boxed set': ('Other', ['Compilation']), 'video': ('Other', []),
    'collaboration': ('Album', []), 'split video': ('Other', []),
}
_MEDIA = {DIGITAL: 'Digital Media', CD: 'CD', VINYL: 'Vinyl', TAPE: 'Cassette'}
BONUS_SUFFIX = ' (Bonus Track)'


def album_id_for(pressing_id):
    """Picard-side id of an MA album. Not UUID-shaped on purpose: Picard auto-moves files only by
    VALID MBIDs, and nothing may ever mistake this for one."""
    return 'metallib-ma-%s' % pressing_id


def build_release(album, pressing, original_date=''):
    """Synthetic release node for Picard's own parser, from the pressing's album page `album`
    (ma_client.parse_album_page) and its row `pressing` in the versions list (may be None).

    All MusicBrainz ids are either '' (no tag at all) or 'metallib-ma-...' placeholders the plugin
    deletes before anything is written -- no fake MBID can reach a file.
    """
    aid = album_id_for(album['album_id'])
    credit = [{'name': album['band'], 'joinphrase': '',
               'artist': {'id': '', 'name': album['band'], 'sort-name': album['band']}}]
    primary, secondary = _TYPES.get(album.get('type', '').lower(), ('Other', []))
    fmt = (pressing or {}).get('format') or album.get('format', '')
    date = ma_date((pressing or {}).get('date') or album.get('date'))
    label = (pressing or {}).get('label') if pressing else album.get('label')
    catalog = (pressing or {}).get('catalog') if pressing else album.get('catalog')

    discs = {}
    for t in album['tracks']:
        discs.setdefault(t['disc'], []).append(t)
    media = []
    for disc in sorted(discs):
        tracks = []
        for pos, t in enumerate(discs[disc], 1):
            title = t['title']
            if t.get('bonus') and 'bonus' not in title.lower():
                title += BONUS_SUFFIX
            length = t['length'] * 1000 if t.get('length') else None
            rid = '%s-%s' % (aid, t.get('song_id') or '%d-%d' % (disc, pos))
            tracks.append({'id': '', 'position': pos, 'number': str(t.get('number') or pos),
                           'title': title, 'length': length, 'artist-credit': credit,
                           'recording': {'id': rid, 'title': title, 'length': length,
                                         'artist-credit': credit, 'relations': []}})
        media.append({'position': disc, 'format': _MEDIA.get(format_kind(fmt), ''),
                      'track-count': len(tracks), 'tracks': tracks})

    node = {'id': aid, 'title': album['album'], 'status': 'Official', 'artist-credit': credit,
            'release-group': {'id': aid + '-rg', 'artist-credit': [], 'primary-type': primary,
                              'secondary-types': secondary,
                              'first-release-date': original_date or date},
            'media': media}
    if date:
        node['date'] = date
    if label or catalog:
        node['label-info'] = [{'label': {'name': label or ''}, 'catalog-number': catalog or ''}]
    return node


# -- choosing the album among search hits --------------------------------------------------------

def title_key(title):
    """Fold spelling variants of one title together: case, accents, "&"/"and"/"+", punctuation,
    a leading "The". "Intercourse And Lust" == "Intercourse & Lust"."""
    s = unicodedata.normalize('NFKD', title or '').lower()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'\s*[&+]\s*', ' and ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return re.sub(r'^the ', '', s)


def title_score(a, b):
    import difflib
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def rank_hits(hits, band, album):
    """Search hits best first: (score, hit). Score = album title similarity, plus a small bonus
    when the band name matches too (several bands share a name, e.g. four "Abigail"s)."""
    scored = []
    for h in hits:
        score = title_score(h.get('album'), album)
        if title_key(h.get('band')) == title_key(band):
            score += 0.05
        scored.append((score, h))
    scored.sort(key=lambda sh: -sh[0])
    return scored


def auto_pick(ranked):
    """The hit to take without asking, or None: the best must be an (almost) exact title match and
    clearly ahead of the next one -- two bands with the same album title still get asked."""
    if not ranked:
        return None
    best = ranked[0][0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best >= 0.95 and runner_up < best - 0.1:
        return ranked[0][1]
    return None

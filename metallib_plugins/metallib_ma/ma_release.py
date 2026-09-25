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
    # Discogs calls digital releases "File" ("File, FLAC, Album"); vinyl "2xLP", tape "Cass".
    # Discogs version lists often omit "File" and only name the codec ("ALAC, Album, Stereo").
    if 'digital' in f or re.search(r'\b(?:file|flac|alac|mp3|aac|wav|aiff|ogg|opus)\b', f):
        return DIGITAL
    if 'vinyl' in f or re.search(r'\b(7|10|12)"', f) or re.search(r'\b(?:\d+x)?lp\b', f):
        return VINYL
    if 'cassette' in f or 'tape' in f or re.search(r'\b(?:\d+x)?cass\b', f):
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
    low = (folder_name or '').lower().replace('_', ' ')      # scene names glue tokens with "_"
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
        # Nothing on the same media: prefer CD-like / unknown formats over vinyl and tape, whose
        # side-based numbering (A1, B2 -> discs) fits a digital or CD copy worst.
        rank = {CD: 0, '': 1, DIGITAL: 1, VINYL: 2, TAPE: 3}
        return sorted(versions, key=lambda v: rank.get(format_kind(v.get('format')), 1)), ''
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


def original_date(dates):
    """The first release from MA's version dates (ma_date form): the earliest YEAR, and within it
    the earliest of the most precise dates -- a version listed only as "2007" must not hide the
    original's "2007-02-19" (plain text order would pick "2007")."""
    dates = [d for d in dates if d and re.match(r'^\d{4}', d)]
    if not dates:
        return ''
    year = min(d[:4] for d in dates)
    same = [d for d in dates if d.startswith(year)]
    most = max(len(d) for d in same)
    return min(d for d in same if len(d) == most)


def pressing_id_of(album_id):
    """The MA pressing id in a MetalLib album id, or None for any other id."""
    s = str(album_id or '')
    return s[len('metallib-ma-'):] if s.startswith('metallib-ma-') else None


def country_code(name):
    """A BAND's country as the folder names it: "Norway" / "NO" -> "NO", "International" -> "XW",
    "Unknown" -> "XU" (user: every artist folder has a code); '' when not recognised."""
    from picard.const.countries import RELEASE_COUNTRIES
    wanted = (name or '').strip().lower()
    if wanted in ('international', 'worldwide', 'xw'):
        return 'XW'
    if wanted in ('unknown', 'n/a', 'xu'):
        return 'XU'
    if len(wanted) == 2 and wanted.isalpha():
        return wanted.upper()
    for code, country in RELEASE_COUNTRIES.items():
        if country.lower() == wanted and len(code) == 2 and code not in ('XW', 'XE', 'XU'):
            return code
    return ''


# Discogs / MA spellings that are not a country's name in Picard's list.
_COUNTRY_ALIASES = {'europe': 'XE', 'worldwide': 'XW', 'uk': 'GB', 'usa': 'US', 'us': 'US',
                    'russia': 'RU', 'south korea': 'KR', 'czech republic': 'CZ', 'iran': 'IR'}


def release_country_code(name):
    """A release country as MusicBrainz writes it: "Argentina" -> "AR", "Europe" -> "XE",
    "Worldwide" -> "XW"; '' when unknown or not one country ("USA & Europe")."""
    wanted = (name or '').strip().lower()
    if not wanted:
        return ''
    if wanted in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[wanted]
    if len(wanted) == 2 and wanted.isalpha():
        return wanted.upper()
    from picard.const.countries import RELEASE_COUNTRIES
    for code, country in RELEASE_COUNTRIES.items():
        if country.lower() == wanted:
            return code
    return ''


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
            # A stable id per track: a saved session puts each file back on its track by this id.
            tracks.append({'id': '%s-t%d-%d' % (aid, disc, pos), 'position': pos,
                           'number': str(t.get('number') or pos),
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
    # The pressing's description ("Limited edition, Digipak") -> ~releasecomment / ~releasepackaging
    # for the naming script's (Lim. Ed.) / (Digipak), as for MusicBrainz releases.
    desc = ((pressing or {}).get('desc') or '').strip()
    if desc:
        node['disambiguation'] = desc
        if 'digipak' in desc.lower() or 'digipack' in desc.lower():
            node['packaging'] = 'Digipak'
    return node


# -- choosing the album among search hits --------------------------------------------------------

_DOTTED_RE = re.compile(r'(?<![A-Za-z0-9])((?:[A-Za-z0-9]\.){2,}[A-Za-z0-9]?)\.?(?![A-Za-z0-9])')


_BONUS_RE = re.compile(r'\s*[(\[]\s*bonus(?:\s+track)?\s*[)\]]\s*$', re.I)


def title_key(title):
    """Fold spelling variants of one title together: case, accents, "&"/"and"/"+", punctuation,
    a leading "The", dotted acronyms ("N.Y.C. 93" == "NYC 93").
    "Intercourse And Lust" == "Intercourse & Lust". A trailing "(Bonus Track)" is an annotation
    (MetalLib adds it from MA), not part of the title."""
    s = _BONUS_RE.sub('', title or '')
    s = _DOTTED_RE.sub(lambda m: m.group(1).replace('.', ''), s)
    s = unicodedata.normalize('NFKD', s).lower()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'\s*[&+]\s*', ' and ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return re.sub(r'^the ', '', s)


def _initialism(short, long):
    """'T.O.M.B.' / 'TOMB' abbreviates 'Target on My Back' (one letter per word)."""
    s = (short or '').strip()
    if not re.fullmatch(r'(?:[A-Za-z0-9]\.){2,}[A-Za-z0-9]?\.?|[A-Z0-9]{3,8}', s):
        return False
    acro = re.sub(r'[^A-Za-z0-9]', '', s).lower()
    words = re.findall(r'[A-Za-z0-9]+', long or '')
    return len(acro) >= 3 and len(words) == len(acro) and ''.join(w[0] for w in words).lower() == acro


def title_score(a, b):
    import difflib
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb or ka.replace(' ', '') == kb.replace(' ', '') or _initialism(a, b) or _initialism(b, a):
        return 1.0                  # also "NYC 93" == "NYC93"
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


# -- pairing one release's tracks with another's (for the per-source columns) --------------------

PAIR_TITLE = 0.8     # title score that pairs two tracks on its own (with lengths not contradicting)
PAIR_LEN_OK_S = 5    # lengths this close agree
PAIR_LEN_BAD_S = 10  # lengths further apart contradict


def pair_tracks(targets, sources):
    """One-to-one pairing of `targets` with `sources`, both lists of {title, length (ms, 0 =
    unknown), disc, number}. Returns {target index: source index}; a target with no clear partner
    is simply left out -- never guessed.

    A pair needs a matching title whose lengths do not contradict, or -- for untitled tracks --
    the same disc/number AND agreeing lengths. Ties are broken by disc/number; still tied -> none.
    """
    def delta(a, b):
        la, lb = a.get('length') or 0, b.get('length') or 0
        return abs(la - lb) / 1000.0 if la and lb else None

    # Same pressing, same tracklist (user, 2026-09-23: "if you load a certain album you just load
    # each of its tracks whatever name they have"): same track count AND every position confirmed
    # IN ORDER -> pair by position. Position is never trusted alone (the old file-swap bug): each
    # pair is confirmed by its length, or -- where the lengths differ (another mix of a bonus track)
    # -- by the same title; and at least half the positions are confirmed by length.
    if targets and len(targets) == len(sources):
        by_length = 0
        for t, s in zip(targets, sources):
            d = delta(t, s)
            if d is not None and d <= PAIR_LEN_OK_S:
                by_length += 1
            elif d is not None and title_score(t.get('title'), s.get('title')) < PAIR_TITLE:
                break
            elif d is None and t.get('title') and s.get('title')                     and title_score(t.get('title'), s.get('title')) < PAIR_TITLE:
                break           # no length to confirm it: the titles must (audit part 1 M3)
        else:
            if by_length * 2 >= len(targets):
                return {i: i for i in range(len(targets))}

    picks = {}
    for ti, t in enumerate(targets):
        cands = []
        for si, s in enumerate(sources):
            d = delta(t, s)
            if d is not None and d > PAIR_LEN_BAD_S:
                continue
            if title_score(t.get('title'), s.get('title')) >= PAIR_TITLE:
                cands.append(si)
        if not cands:
            same = [si for si, s in enumerate(sources)
                    if (s.get('disc'), s.get('number')) == (t.get('disc'), t.get('number'))
                    and delta(t, s) is not None and delta(t, s) <= PAIR_LEN_OK_S]
            cands = same
        if len(cands) > 1:
            same_pos = [si for si in cands
                        if (sources[si].get('disc'), sources[si].get('number')) == (t.get('disc'), t.get('number'))]
            cands = same_pos if len(same_pos) == 1 else []
        if len(cands) == 1:
            picks[ti] = cands[0]
    # One-to-one: a source claimed by two targets pairs with neither.
    claimed = {}
    for ti, si in picks.items():
        claimed.setdefault(si, []).append(ti)
    return {ti: si for ti, si in picks.items() if len(claimed[si]) == 1}


def track_count_compatible(a, b):
    """Port of full_program _mb_track_count_compatible: the smaller count must be >= 70% of the
    larger (a bonus/deluxe edition still fits, an EP vs an album does not). Unknown -> True."""
    a, b = int(a or 0), int(b or 0)
    if not a or not b:
        return True
    return min(a, b) >= 0.7 * max(a, b)


def mb_genres(release_node, limit=3):
    """Genres for the MusicBrainz column: the release's own, else its release group's, else the
    release artists' -- the most-voted ones (at least half the top vote), title-cased."""
    levels = [release_node.get('genres'), (release_node.get('release-group') or {}).get('genres')]
    artists = []
    for credit in release_node.get('artist-credit') or []:
        artists.extend((credit.get('artist') or {}).get('genres') or [])
    levels.append(artists)
    for genres in levels:
        genres = [g for g in genres or [] if g.get('name')]
        if not genres:
            continue
        counts = {}
        for g in genres:
            counts[g['name']] = counts.get(g['name'], 0) + (g.get('count') or 1)
        top = max(counts.values())
        names = sorted((n for n, c in counts.items() if c >= top / 2), key=lambda n: (-counts[n], n))
        return [n.title() for n in names[:limit]]
    return []


# -- lineup -> credit tags ------------------------------------------------------------------------

# MA role -> Picard tag, for roles that are not instruments/vocals. Same names Picard uses for
# MusicBrainz relationships (mbjson._ARTIST_REL_TYPES), so the two source columns line up.
_ROLE_TAGS = {'lyrics': 'lyricist', 'music': 'composer', 'songwriting': 'writer', 'producer': 'producer',
              'production': 'producer', 'co-producer': 'producer', 'engineering': 'engineer',
              'recording': 'engineer', 'sound engineering': 'engineer', 'mixing': 'mixer'}
# Staff roles with no standard tag: left out rather than invented.
_SKIP_ROLES = ('art', 'layout', 'photo', 'design', 'logo', 'mastering', 'management', 'booking',
               'liner', 'executive', 'a&r', 'coordination', 'translation')
# Instrument words MA writes in the plural.
_SINGULAR = {'guitars': 'guitar', 'keyboards': 'keyboard', 'synthesizers': 'synthesizer',
             'samples': 'samples', 'effects': 'effects', 'drums': 'drums', 'vocals': 'vocals'}


def _split_roles(text):
    """"Guitars, Lyrics (tracks 1-8, 10, 11)" -> ["Guitars", "Lyrics (tracks 1-8, 10, 11)"]."""
    out, depth, cur = [], 0, ''
    for ch in text:
        depth += ch == '('
        depth -= ch == ')'
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _track_numbers(qualifier):
    """"tracks 1-8, 10, 11" -> {1..8, 10, 11}; no numbers -> None (the role applies everywhere)."""
    nums = set()
    for m in re.finditer(r'(\d+)\s*(?:-|–|to)\s*(\d+)|(\d+)', qualifier):
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(3))
        nums.update(range(int(a), int(b) + 1))
    return nums or None


# Words that make a role a PERFORMANCE (an instrument or the voice). Anything else in a lineup that
# is not a known staff role (_ROLE_TAGS) is skipped rather than invented as "performer:<role>".
_INSTRUMENT_WORDS = ('vocal', 'voice', 'choir', 'guitar', 'bass', 'drum', 'percussion', 'keyboard',
                     'synth', 'piano', 'organ', 'violin', 'viola', 'cello', 'flute', 'trumpet', 'saxophone',
                     'trombone', 'horn', 'harp', 'accordion', 'mandolin', 'banjo', 'bagpipe', 'whistle',
                     'harmonica', 'sitar', 'oud', 'bouzouki', 'lute', 'clarinet', 'oboe', 'bassoon', 'tuba',
                     'sample', 'effects', 'noise', 'electronics', 'turntable', 'programming', 'orchestra',
                     'strings', 'narration', 'spoken', 'growl', 'scream', 'chant', 'theremin', 'didgeridoo')


def is_instrument(role):
    role = (role or '').lower()
    return any(w in role for w in _INSTRUMENT_WORDS)


# MA writes notes into a member's name: "Martín Carrizo (R.I.P. 2022)". Not part of the name.
_NAME_NOTE_RE = re.compile(r'\s*\((?:R\.?\s*I\.?\s*P\.?|RIP|†|deceased|died)\b[^)]*\)', re.I)
# MA's wording -> the one MusicBrainz/Picard write, so one album does not mix both.
_INSTRUMENT_ALIASES = {'backing vocals': 'background vocals', 'backing vocal': 'background vocals'}


def person_name(name):
    return _NAME_NOTE_RE.sub('', name or '').strip()


def lineup_tags(lineup, track_position):
    """Credit tags for the track at absolute position `track_position` (1-based, across discs --
    MA's "(tracks 1-8, 10)" counts that way): {tag: [names]}."""
    tags = {}
    for person in lineup or []:
        for role in _split_roles(person['roles']):
            m = re.match(r'^(.*?)\s*\((.*)\)\s*$', role)
            base, qual = (m.group(1), m.group(2)) if m else (role, '')
            numbers = _track_numbers(qual) if re.search(r'\btracks?\b|\bon\b', qual, re.I) else None
            if numbers is not None and track_position not in numbers:
                continue
            extra = '' if numbers is not None or not qual else qual.strip().lower()   # "(additional)", "(lead)"
            key = base.strip().lower()
            if not key or any(k in key for k in _SKIP_ROLES):
                continue
            if key in _ROLE_TAGS:
                tag = _ROLE_TAGS[key]
            elif not is_instrument(key):
                continue            # "Cover concept", "Pre-production", ...: a credit, not a performance
            else:
                instrument = ' '.join(_SINGULAR.get(w, w) for w in key.split())
                if extra:
                    instrument = '%s %s' % (extra, instrument)
                # after "Vocals (backing)" became "backing vocals"
                instrument = _INSTRUMENT_ALIASES.get(instrument, instrument)
                if person['section'] == 'guest':
                    instrument = 'guest ' + instrument
                tag = 'performer:' + instrument
            names = tags.setdefault(tag, [])
            name = person_name(person['name'])
            if name and name not in names:
                names.append(name)
    return tags

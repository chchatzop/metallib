# MetalLib -- turn a Discogs release into a Picard release node (for the "Discogs" source column).
#
# Pure logic, no Picard or network imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re

from .ma_release import (
    CD,
    DIGITAL,
    TAPE,
    VINYL,
    format_kind,
    release_country_code,
)


def clean_name(name):
    """Discogs disambiguation: "Abigail (2)" -> "Abigail"; an ANV marker "Name*" -> "Name"."""
    return re.sub(r'\s*\(\d+\)$', '', (name or '').strip()).rstrip('*').strip()


def artist_credit(artists):
    out = ''
    for a in artists or []:
        out += (a.get('anv') or clean_name(a.get('name'))) + (' %s ' % a['join'] if a.get('join') and a['join'] != ',' else
                                                              (', ' if a.get('join') == ',' else ''))
    return out.strip()


def seconds(duration):
    parts = [p for p in (duration or '').split(':') if p.isdigit()]
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


_POS_DISC_RE = re.compile(r'^(?:CD|DVD|Disc|D)?\s*(\d+)\s*[-.]\s*(\d+)$', re.I)
_POS_SIDE_RE = re.compile(r'^([A-Z])(\d*)(?:[a-z]|\.\d+)?$')     # A1, B2, C1a (sub-track), D1.2


def flat_tracklist(release):
    """[{disc, number, position, title, length (s), extraartists}] -- headings skipped, index tracks
    expanded into their sub-tracks, vinyl sides paired into discs (A/B = 1, C/D = 2 ...)."""
    raw = []
    for t in release.get('tracklist') or []:
        kind = t.get('type_', 'track')
        if kind == 'heading':
            continue
        if kind == 'index' and t.get('sub_tracks'):
            for sub in t['sub_tracks']:
                if sub.get('type_', 'track') == 'track':
                    raw.append(dict(sub, title='%s: %s' % (t.get('title'), sub.get('title'))
                                    if t.get('title') else sub.get('title')))
            continue
        if kind == 'track':
            raw.append(t)
    out, counters = [], {}
    for t in raw:
        pos = (t.get('position') or '').strip()
        disc = 1
        m = _POS_DISC_RE.match(pos)
        s = _POS_SIDE_RE.match(pos)
        if m:
            disc = int(m.group(1))
        elif s:
            disc = (ord(s.group(1)) - ord('A')) // 2 + 1
        counters[disc] = counters.get(disc, 0) + 1
        out.append({'disc': disc, 'number': counters[disc], 'position': pos, 'title': t.get('title') or '',
                    'length': seconds(t.get('duration')), 'extraartists': t.get('extraartists') or []})
    return out


# Discogs credit role -> Picard tag (the names Picard uses for MusicBrainz relationships).
_ROLE_TAGS = {'lyrics by': 'lyricist', 'music by': 'composer', 'written-by': 'writer', 'written by': 'writer',
              'songwriter': 'writer', 'composed by': 'composer', 'producer': 'producer',
              'co-producer': 'producer', 'engineer': 'engineer', 'recorded by': 'engineer',
              'recording engineer': 'engineer', 'mixed by': 'mixer', 'mixing engineer': 'mixer'}
_SKIP = ('artwork', 'design', 'layout', 'photo', 'logo', 'mastered', 'mastering', 'lacquer', 'liner',
         'management', 'executive', 'a&r', 'booking', 'translated', 'illustration', 'cover')
_INSTRUMENTS = {'vocals': 'vocals', 'lead vocals': 'lead vocals', 'backing vocals': 'background vocals',
                'guitar': 'guitar', 'lead guitar': 'lead guitar', 'rhythm guitar': 'rhythm guitar',
                'bass': 'bass', 'bass guitar': 'bass', 'drums': 'drums', 'keyboards': 'keyboard',
                'synthesizer': 'synthesizer', 'percussion': 'percussion', 'violin': 'violin', 'cello': 'cello',
                'flute': 'flute', 'piano': 'piano', 'organ': 'organ', 'acoustic guitar': 'acoustic guitar'}


def _role_tags(role):
    """"Guitar [Lead], Vocals" -> ['performer:lead guitar', 'performer:vocals']."""
    tags = []
    for part in re.split(r',\s*(?![^\[]*\])', role or ''):
        part = part.strip()
        m = re.match(r'^(.*?)\s*\[(.*)\]\s*$', part)
        base, attr = (m.group(1), m.group(2)) if m else (part, '')
        key = base.strip().lower()
        if not key or any(k in key for k in _SKIP):
            continue
        if key in _ROLE_TAGS:
            tags.append(_ROLE_TAGS[key])
            continue
        instrument = _INSTRUMENTS.get(key)
        if instrument is None and ('guitar' in key or 'vocal' in key or key in ('drums', 'bass')):
            instrument = key
        if instrument:
            if attr and attr.lower() not in instrument:
                instrument = '%s %s' % (attr.lower(), instrument)
            tags.append('performer:' + instrument)
    return tags


def _applies(tracks_spec, position, index):
    """Release-level credit "tracks" field ("1 to 3, 5", "A1, B2") -- empty = every track."""
    spec = (tracks_spec or '').strip()
    if not spec:
        return True
    for chunk in re.split(r',\s*', spec):
        m = re.match(r'^(\S+)\s+to\s+(\S+)$', chunk, re.I)
        if m:
            lo, hi = m.group(1), m.group(2)
            if lo.isdigit() and hi.isdigit() and str(index).isdigit() and int(lo) <= index <= int(hi):
                return True
            if lo <= position <= hi and len(lo) == len(position):
                return True
        elif chunk == position or (chunk.isdigit() and int(chunk) == index):
            return True
    return False


def credit_tags(release, track, index):
    """{tag: [names]} for one flat track (index = 1-based position in the whole release)."""
    tags = {}
    credits = [(c, c.get('tracks')) for c in release.get('extraartists') or []]
    credits += [(c, '') for c in track.get('extraartists') or []]
    for credit, spec in credits:
        if not _applies(spec, track['position'], index):
            continue
        name = credit.get('anv') or clean_name(credit.get('name'))
        for tag in _role_tags(credit.get('role')):
            names = tags.setdefault(tag, [])
            if name not in names:
                names.append(name)
    return tags


_MEDIA = {DIGITAL: 'Digital Media', CD: 'CD', VINYL: 'Vinyl', TAPE: 'Cassette'}


# Format descriptions that only restate the release type or media, not an edition.
_PLAIN_DESCRIPTIONS = {'album', 'lp', 'ep', 'single', 'mini-album', 'compilation', 'stereo', 'mono',
                       '12"', '7"', '10"', '33 ⅓ rpm', '45 rpm', 'cd', 'cdr'}


def build_node(release):
    """Picard release node for a Discogs release (ids are "metallib-dg-..." placeholders the caller
    removes). Credits and genres are returned separately: {node, credits: [{tag: names}], genres}."""
    rid = 'metallib-dg-%s' % release.get('id')
    band = artist_credit(release.get('artists'))
    credit = [{'name': band, 'joinphrase': '', 'artist': {'id': '', 'name': band, 'sort-name': band}}]
    flat = flat_tracklist(release)
    fmt = ((release.get('formats') or [{}])[0]).get('name', '')
    media, per_track_credits = {}, []
    for i, t in enumerate(flat, 1):
        length = t['length'] * 1000 or None
        media.setdefault(t['disc'], []).append({
            'id': '', 'position': t['number'], 'number': t['position'] or str(t['number']), 'title': t['title'],
            'length': length, 'artist-credit': credit,
            'recording': {'id': '%s-%d' % (rid, i), 'title': t['title'], 'length': length,
                          'artist-credit': credit, 'relations': []}})
        per_track_credits.append(credit_tags(release, t, i))
    label = (release.get('labels') or [{}])[0]
    node = {'id': rid, 'title': release.get('title') or '', 'status': 'Official', 'artist-credit': credit,
            # No first-release-date: a Discogs release's date is its pressing's, not the original's.
            'release-group': {'id': rid + '-rg', 'artist-credit': [], 'primary-type': 'Album'},
            'media': [{'position': d, 'format': _MEDIA.get(format_kind(fmt), fmt), 'track-count': len(ts),
                       'tracks': ts} for d, ts in sorted(media.items())]}
    date = release.get('released') or ''
    if re.match(r'^\d{4}(-\d{2}(-\d{2})?)?$', date.replace('-00', '')):
        node['date'] = date.replace('-00', '')
    elif release.get('year'):
        node['date'] = str(release['year'])
    if label:
        node['label-info'] = [{'label': {'name': clean_name(label.get('name'))},
                               'catalog-number': '' if (label.get('catno') or '').lower() == 'none' else label.get('catno', '')}]
    code = release_country_code(release.get('country'))
    if code:
        node['country'] = code             # Discogs gives "Argentina"; the tag wants "AR" (user review)
    # "Limited Edition, Deluxe Edition, Digipak" -> ~releasecomment / ~releasepackaging, which the
    # naming script turns into (Lim. Ed.) (Del. Ed.) (Digipak), as for MusicBrainz releases.
    notes = []
    for f in release.get('formats') or []:
        notes += [d for d in f.get('descriptions') or [] if d.lower() not in _PLAIN_DESCRIPTIONS]
        if f.get('text'):
            notes.append(f['text'])
    notes = list(dict.fromkeys(notes))
    if notes:
        node['disambiguation'] = ', '.join(notes)
    if any('digipak' in n.lower() or 'digipack' in n.lower() for n in notes):
        node['packaging'] = 'Digipak'
    barcodes = [i.get('value', '') for i in release.get('identifiers') or [] if i.get('type') == 'Barcode']
    if barcodes:
        node['barcode'] = re.sub(r'\s+', '', barcodes[0])
    genres = release.get('styles') or release.get('genres') or []
    return {'node': node, 'credits': per_track_credits, 'genres': genres}

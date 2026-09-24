# MetalLib -- the pressings each source offers for an album, ranked best match first.
#
# Shown in the Pressings panel (user idea, 2026-09-24): one list per source; exact track-count
# matches first (best on top), near misses greyed below; clicking one loads it into that source's
# column. The choice is not saved (user: "don't save").
#
# Pure logic, no Picard or network imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from .ma_release import (
    catalog_matches,
    format_kind,
    ma_date,
    media_hint,
)


def candidate(source, cid, date='', fmt='', label='', catalog='', country='', desc='', track_count=None,
              fits=None):
    """One pressing as shown in a list. track_count / fits stay None until known."""
    return {'source': source, 'id': str(cid), 'date': date or '', 'format': fmt or '', 'label': label or '',
            'catalog': catalog or '', 'country': country or '', 'desc': desc or '',
            'track_count': track_count, 'fits': fits}


def score(c, local):
    """Evidence that this pressing is the local copy: catalog named in the folder/tags, same media,
    same year, lengths that fit. Track count is handled by the grouping in rank()."""
    s = 0
    if catalog_matches(c['catalog'], local.get('folder', ''), local.get('catalog', '')):
        s += 4
    hint = media_hint(local.get('folder', ''), local.get('media', ''))
    if hint and format_kind(c['format']) == hint:
        s += 2
    if local.get('year') and local['year'] in (c['date'] or ''):
        s += 1
    if c['fits'] is True:
        s += 3
    elif c['fits'] is False:
        s -= 3
    return s


def rank(candidates, local):
    """[(candidate, greyed)] best first: exact track count, then unknown count, then near misses
    (greyed). Within each group by score."""
    want = local.get('track_count') or 0

    def group(c):
        if not want or c['track_count'] is None:
            return 1
        return 0 if c['track_count'] == want else 2

    ordered = sorted(candidates, key=lambda c: (group(c), -score(c, local)))
    return [(c, group(c) == 2) for c in ordered]


def unmatched(c, want):
    """Same track count as the album, but its lengths do not fit the files (shown in red)."""
    return c['fits'] is False and c['track_count'] == want


def describe(c, want=0):
    """One-line text: "2026 · CD · Nuclear Blast · NB 123-2 · Europe · 11 tracks". Only a problem is
    spelled out ("lengths unmatched"); fitting lengths say nothing (user: saves space)."""
    parts = [p for p in (c['date'][:10] if c['date'] else '', c['format'], c['label'], c['catalog'],
                         c['country']) if p]
    if c['track_count'] is not None:
        tail = '%d tracks' % c['track_count']
        if unmatched(c, want):
            tail += ', lengths unmatched'
        parts.append(tail)
    else:
        parts.append('checking...')
    return ' · '.join(parts)


# -- each source's raw pressing data -> candidates ---------------------------------------------------------

def from_ma(versions, checked=()):
    """Metal Archives versions (ma_client.parse_versions) + pages already fetched to judge fit."""
    known = {str(c['version']['album_id']): c for c in checked}
    out = []
    for v in versions:
        c = known.get(str(v['album_id']))
        out.append(candidate('Metal Archives', v['album_id'], ma_date(v.get('date') or '') or v.get('date'),
                             v.get('format'), v.get('label'),
                             v.get('catalog'), '', v.get('desc'),
                             len(c['page']['tracks']) if c else None, c['fits'] if c else None))
    return out


def from_mb(releases):
    """MusicBrainz release list (browse by release group, or a search) -- inc media + labels."""
    out = []
    for r in releases:
        media = r.get('media') or []
        formats = [m.get('format') or '?' for m in media]
        fmt = ' + '.join('%d×%s' % (formats.count(f), f) if formats.count(f) > 1 else f
                         for f in dict.fromkeys(formats))
        info = (r.get('label-info') or [{}])[0] or {}
        count = sum(m.get('track-count') or 0 for m in media) or r.get('track-count')
        desc = ' '.join(p for p in (r.get('disambiguation'), r.get('status') if r.get('status') != 'Official' else '')
                        if p)
        catalog = info.get('catalog-number') or ''
        if catalog.lower() == '[none]':                  # MusicBrainz's "has no catalog number"
            catalog = ''
        out.append(candidate('MusicBrainz', r['id'], r.get('date'), fmt, ((info.get('label') or {}).get('name')),
                             catalog, r.get('country'), desc, count or None))
    return out


def from_discogs(versions):
    """Discogs master versions (/masters/{id}/versions)."""
    # "format" holds only the descriptions ("Album, Limited Edition"); the media is in major_formats.
    def fmt(v):
        major = [m for m in v.get('major_formats') or [] if m]
        return ', '.join(dict.fromkeys(major + [p.strip() for p in (v.get('format') or '').split(',') if p.strip()]))
    return [candidate('Discogs', v['id'], v.get('released'), fmt(v), v.get('label'), v.get('catno'),
                      v.get('country'))
            for v in versions if v.get('id')]


def better_pick(candidates, chosen_id, want):
    """The pressing to switch to automatically, or None. `candidates` best first (rank()).
    - the shown one does not fit and exactly one pressing has the album's track count AND fitting
      lengths (same rule as the first Metal Archives pick) -> that one;
    - else, the shown one has another track count while some pressing has the album's -> the best
      of those (a same-count pressing, even with unmatched lengths, beats a near miss)."""
    shown = next((c for c in candidates if c['id'] == chosen_id), None)
    if shown is not None and shown['fits'] is True and shown['track_count'] == want:
        return None
    fitting = [c for c in candidates if c['track_count'] == want and c['fits'] is True]
    if len(fitting) == 1:
        return fitting[0]['id']
    if shown is not None and shown['track_count'] is not None and shown['track_count'] != want:
        same = [c for c in candidates if c['track_count'] == want]
        if same:
            return same[0]['id']
    return None


def short_label(c):
    """What a source column shows, for its header: "CD · SOM 532B · 2019 · XE"."""
    return ' · '.join(p for p in (c['format'], c['catalog'], c['date'][:4], c['country']) if p)

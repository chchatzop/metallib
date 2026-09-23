# MetalLib -- Metal Archives client: polite live fetching + parsing + MetalLib's own cache.
#
# Ported from the user's Tag & Rename tool (ma_live.py) with one deliberate change: the tracklist
# parser keeps DISC and TRACK NUMBERS (ma_live flattened every disc into one list).
#
# Politeness (Metal Archives is a volunteer site): one request at a time, a random 0.6-0.8 s gap
# between requests, a real browser fingerprint via curl_cffi (plain requests/urllib get a
# Cloudflare 403), and every page is cached so it is never fetched twice.
#
# No Picard imports -- unit-testable on its own.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import html
import json
import random
import re
import sqlite3
import threading
import time


BASE = 'https://www.metal-archives.com'
TIMEOUT = 30
MIN_INTERVAL, MAX_INTERVAL = 0.6, 0.8      # user-tested 2026-09-24: 0.6 s is fine; a little jitter kept
CACHE_DAYS = 30                    # re-fetch a cached page after this long


class MAError(Exception):
    """Transient failure (network, Cloudflare challenge, bad status)."""


class MANotFound(MAError):
    """HTTP 404: the page does not exist (removed album/band)."""


# ------------------------------------------------------------------------------------------------
# Parsing (pure functions over page text)
# ------------------------------------------------------------------------------------------------

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')
_NULLS = {'n/a', 'n.a.', '-', '—', 'none', 'unknown'}


def _text(fragment):
    return _WS_RE.sub(' ', html.unescape(_TAG_RE.sub(' ', fragment or ''))).strip()


def _null(value):
    return '' if value.strip().lower() in _NULLS else value.strip()


_ID_LINK_RE = re.compile(r'<a href="[^"]*?/(\d+)"[^>]*>(.*?)</a>', re.S)
_ALBUM_ID_RE = re.compile(r'/albums/[^/"\s]+/[^/"\s]+/(\d+)')   # three explicit segments (see note)
# Note: the old non-greedy r'/albums/[^"\s]*?/(\d+)' captured "9" from /albums/9_Dead/9_Dead/1089842
# and poisoned 12,657 pressing rows in the Tag & Rename tool. Never loosen this.


def parse_album_search(text):
    """ajax-advanced album search JSON -> [{band_id, band, band_country, album_id, album, type}]."""
    data = json.loads(text)
    out = []
    for row in data.get('aaData') or []:
        if len(row) < 2:
            continue
        band = _ID_LINK_RE.search(row[0] or '')
        album = _ID_LINK_RE.search(row[1] or '')
        if not band or not album:
            continue
        country = re.search(r'title="[^"]*\(([^)]+)\)"', row[0] or '')
        out.append({
            'band_id': band.group(1), 'band': _text(band.group(2)),
            'band_country': country.group(1) if country else '',
            'album_id': album.group(1), 'album': _text(album.group(2)),
            'type': _text(row[2]) if len(row) > 2 else '',
        })
    return out


_DL_RE = re.compile(r'<dl[^>]*class="float_[^"]*"[^>]*>(.*?)</dl>', re.S | re.I)
_DT_DD_RE = re.compile(r'<dt>([^<]+)</dt>\s*<dd[^>]*>(.*?)</dd>', re.S)


def _info(page):
    scope = ''.join(_DL_RE.findall(page)) or page
    return {k.strip().rstrip(':').lower(): v for k, v in _DT_DD_RE.findall(scope)}


_ROW_RE = re.compile(r'<tr(\s[^>]*)?>(.*?)</tr>', re.S)     # (attributes, body)
_NUM_RE = re.compile(r'</a>\s*(\d+)\.\s*</td>')
_TITLE_RE = re.compile(r'<td class="wrapWords\b([^"]*)"[^>]*>(.*?)</td>', re.S)
_TIME_RE = re.compile(r'<td align="right">\s*(\d{1,3}:\d{2}(?::\d{2})?)\s*</td>')
_SONG_ID_RE = re.compile(r'<a name="([0-9A-Za-z]+)" class="anchor">')


def parse_tracklist(page):
    """Album page -> [{disc, side, number, title, length (s, 0 = unknown), bonus, song_id}].
    Discs come from "Disc N" rows (default 1); numbers restart per disc as MA prints them."""
    tracks, disc, side = [], 1, ''
    body = page.split('id="album_tabs_tracklist"', 1)[-1].split('id="album_tabs_lineup"', 1)[0]
    for cls, row in _ROW_RE.findall(body):
        if 'discRow' in (cls or ''):
            m = re.search(r'(\d+)', _text(row))
            disc = int(m.group(1)) if m else disc + 1
            side = ''
            continue
        if 'sideRow' in (cls or ''):
            side = _text(row).replace('Side', '').strip()
            continue
        t = _TITLE_RE.search(row)
        n = _NUM_RE.search(row)
        if not t or not n:
            continue                                    # lyrics rows, subtotal rows
        tm = _TIME_RE.search(row, t.end())
        sid = _SONG_ID_RE.search(row)
        tracks.append({
            'disc': disc, 'side': side, 'number': int(n.group(1)), 'title': _text(t.group(2)),
            'length': _seconds(tm.group(1)) if tm else 0,
            'bonus': 'bonus' in t.group(1).lower(), 'song_id': sid.group(1) if sid else '',
        })
    return tracks


def _seconds(hms):
    parts = [int(p) for p in hms.split(':')]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def parse_album_page(page, album_id=''):
    """Album page -> {album_id, album, band_id, band, type, date, year, label, catalog, format,
    tracks, cover_url}."""
    info = _info(page)
    name = re.search(r'<h1 class="album_name">\s*<a[^>]*>(.*?)</a>', page, re.S)
    band = re.search(r'<h2 class="band_name">\s*<a href="[^"]*?/(\d+)"[^>]*>(.*?)</a>', page, re.S)
    date = _text(info.get('release date', ''))
    year = re.search(r'\b(19|20)\d{2}\b', date)
    cover = re.search(r'id="cover"[^>]*href="([^"]+)"', page) or re.search(r'class="image"[^>]*href="([^"]+)"', page)
    return {
        'album_id': album_id, 'album': _text(name.group(1)) if name else '',
        'band_id': band.group(1) if band else '', 'band': _text(band.group(2)) if band else '',
        'type': _text(info.get('type', '')), 'date': date, 'year': year.group(0) if year else '',
        'label': _null(_text(info.get('label', ''))), 'catalog': _null(_text(info.get('catalog id', ''))),
        'format': _text(info.get('format', '')), 'tracks': parse_tracklist(page),
        'cover_url': cover.group(1) if cover else '', 'lineup': parse_lineup(page),
    }


_CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)


def parse_versions(page):
    """Other-versions tab -> [{album_id, date, label, catalog, format, desc}] (the album itself included)."""
    out = []
    for _, row in _ROW_RE.findall(page):
        cells = _CELL_RE.findall(row)
        vid = _ALBUM_ID_RE.search(row)
        if len(cells) < 4 or not vid:
            continue
        out.append({'album_id': vid.group(1), 'date': _text(cells[0]),
                    'label': _null(_text(cells[1])), 'catalog': _null(_text(cells[2])),
                    'format': _text(cells[3]), 'desc': _text(cells[4]) if len(cells) > 4 else ''})
    return out


def parse_band_page(page):
    info = _info(page)
    return {'country': _text(info.get('country of origin', '')), 'genre': _text(info.get('genre', '')),
            'status': _text(info.get('status', '')), 'formed': _text(info.get('formed in', ''))}


_CHALLENGE_TOKENS = ('cdn-cgi/challenge-platform', '__cf_chl', 'cf_chl_opt', 'cf-browser-verification')


def looks_like_challenge(text):
    low = (text or '')[:4000].lower()
    # "just a moment" alone is not enough: a real album is titled "Just a Momentary Diversion".
    return (any(t in low for t in _CHALLENGE_TOKENS)
            or ('just a moment' in low and 'enable javascript and cookies' in low))


# ------------------------------------------------------------------------------------------------
# Fetching + cache
# ------------------------------------------------------------------------------------------------

class MAClient:
    """Thread-safe, single-flight Metal Archives client with an sqlite page cache."""

    def __init__(self, cache_path, session_factory=None, sleep=time.sleep, clock=time.time):
        self._lock = threading.Lock()           # one request at a time, globally
        self._last = 0.0
        self._sleep, self._clock = sleep, clock
        self._session_factory = session_factory or _default_session
        self._session = None
        self._db = sqlite3.connect(cache_path, check_same_thread=False)
        self._db.execute('CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, body TEXT, fetched_at REAL)')
        self._db.execute('CREATE TABLE IF NOT EXISTS images (url TEXT PRIMARY KEY, data BLOB, fetched_at REAL)')
        self._db.commit()

    # -- raw ----------------------------------------------------------------------------------
    def fetch(self, url, ajax=False, max_age_days=CACHE_DAYS):
        with self._lock:
            row = self._db.execute('SELECT body, fetched_at FROM pages WHERE url=?', (url,)).fetchone()
            if row and self._clock() - row[1] < max_age_days * 86400:
                return row[0]
            wait = self._last + random.uniform(MIN_INTERVAL, MAX_INTERVAL) - self._clock()
            if wait > 0:
                self._sleep(wait)
            try:
                if self._session is None:
                    self._session = self._session_factory()
                headers = {'X-Requested-With': 'XMLHttpRequest'} if ajax else {}
                resp = self._session.get(url, headers=headers, timeout=TIMEOUT)
            except Exception as e:
                raise MAError('network error: %s' % e) from e
            finally:
                self._last = self._clock()
            if resp.status_code == 404:
                raise MANotFound(url)
            if resp.status_code != 200:
                raise MAError('HTTP %s for %s' % (resp.status_code, url))
            body = resp.text
            if looks_like_challenge(body):
                raise MAError('Cloudflare challenge page for %s' % url)
            # Never cache an empty/garbage page over nothing: it is almost always a silent block.
            if not ajax and len(body) < 2000:
                raise MAError('suspiciously short page for %s' % url)
            self._db.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?)', (url, body, self._clock()))
            self._db.commit()
            return body

    def fetch_bytes(self, url):
        """An image (cover), cached like pages and under the same one-at-a-time throttle."""
        with self._lock:
            row = self._db.execute('SELECT data FROM images WHERE url=?', (url,)).fetchone()
            if row:
                return row[0]
            wait = self._last + random.uniform(MIN_INTERVAL, MAX_INTERVAL) - self._clock()
            if wait > 0:
                self._sleep(wait)
            try:
                if self._session is None:
                    self._session = self._session_factory()
                resp = self._session.get(url, timeout=TIMEOUT)
            except Exception as e:
                raise MAError('network error: %s' % e) from e
            finally:
                self._last = self._clock()
            ctype = (getattr(resp, 'headers', {}) or {}).get('content-type', '')
            if resp.status_code != 200 or not ctype.startswith('image'):
                raise MAError('no image at %s (HTTP %s, %s)' % (url, resp.status_code, ctype))
            self._db.execute('INSERT OR REPLACE INTO images VALUES (?,?,?)', (url, resp.content, self._clock()))
            self._db.commit()
            return resp.content

    # -- typed --------------------------------------------------------------------------------
    def search_albums(self, band, album, limit=40):
        from urllib.parse import urlencode
        q = urlencode({'bandName': band or '', 'releaseTitle': album or '', 'sEcho': 1, 'iColumns': 4,
                       'iDisplayStart': 0, 'iDisplayLength': limit})
        return parse_album_search(self.fetch('%s/search/ajax-advanced/searching/albums?%s' % (BASE, q), ajax=True))

    def album(self, album_id):
        return parse_album_page(self.fetch('%s/albums/_/_/%s' % (BASE, album_id)), album_id)

    def versions(self, album_id):
        return parse_versions(self.fetch(
            '%s/release/ajax-versions/current/%s/parent/%s' % (BASE, album_id, album_id), ajax=True))

    def band(self, band_id):
        return parse_band_page(self.fetch('%s/bands/_/%s' % (BASE, band_id)))


def _default_session():
    from curl_cffi import requests as creq
    s = creq.Session(impersonate='chrome')
    s.headers.update({'Accept': 'text/html,application/xhtml+xml,application/json,*/*;q=0.8',
                      'Accept-Language': 'en-US,en;q=0.9', 'Referer': BASE + '/'})
    return s


_LINEUP_ROW_RE = re.compile(r'<tr class="(lineupHeaders|lineupRow)"[^>]*>(.*?)</tr>', re.S)


def parse_lineup(page):
    """Album page -> [{'name', 'roles' (raw text), 'section'}] from the "Complete lineup" tab.
    section: 'members' (Band members), 'guest' (Guest/Session), 'misc' (Miscellaneous staff)."""
    block = page.split('id="album_all_members_lineup"', 1)
    if len(block) < 2:
        return []
    block = re.split(r'id="album_members_lineup"|id="album_members_misc"|id="album_tabs_reviews"', block[1])[0]
    out, section = [], 'members'
    for kind, row in _LINEUP_ROW_RE.findall(block):
        if kind == 'lineupHeaders':
            head = _text(row).lower()
            if 'guest' in head or 'session' in head:
                section = 'guest'
            elif 'misc' in head or 'staff' in head:
                section = 'misc'
            elif 'member' in head:
                section = 'members'
            continue
        cells = _CELL_RE.findall(row)
        if len(cells) >= 2:
            name, roles = _text(cells[0]), _text(cells[1])
            if name and roles:
                out.append({'name': name, 'roles': roles, 'section': section})
    return out

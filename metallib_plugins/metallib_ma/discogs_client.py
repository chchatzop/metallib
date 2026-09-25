# MetalLib -- Discogs API client (api.discogs.com): polite, cached, token supplied by the user.
#
# Discogs requires a descriptive User-Agent and an access token for database search. The token is
# the user's own (MetalLib options); it is only ever sent to api.discogs.com. One request at a
# time, >= 1.1 s apart (authenticated limit is 60/min), every response cached.
#
# No Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


BASE = 'https://api.discogs.com'
USER_AGENT = 'MetalLib/0.1 +https://github.com/chchatzop/metallib'
MIN_INTERVAL = 1.1
CACHE_DAYS = 30


class DiscogsError(Exception):
    pass


class DiscogsClient:
    def __init__(self, cache_path, token_getter, opener=None, sleep=time.sleep, clock=time.time):
        self.stop = None                # a threading.Event: set -> no new requests (quitting)
        self._token = token_getter
        self._open = opener or urllib.request.urlopen
        self._sleep, self._clock = sleep, clock
        self._lock = threading.Lock()
        self._last = 0.0
        self._db = sqlite3.connect(cache_path, check_same_thread=False)
        self._db.execute('CREATE TABLE IF NOT EXISTS responses (url TEXT PRIMARY KEY, body TEXT, fetched_at REAL)')
        self._db.commit()

    def has_token(self):
        return bool((self._token() or '').strip())

    def get(self, path, params=None):
        url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        with self._lock:
            row = self._db.execute('SELECT body, fetched_at FROM responses WHERE url=?', (url,)).fetchone()
            if row and self._clock() - row[1] < CACHE_DAYS * 86400:
                return json.loads(row[0])
            token = (self._token() or '').strip()
            if not token:
                raise DiscogsError('no Discogs token set (MetalLib options)')
            if self.stop is not None and self.stop.is_set():
                raise DiscogsError('MetalLib is closing')
            wait = self._last + MIN_INTERVAL - self._clock()
            if wait > 0:
                self._sleep(wait)
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json',
                                                       'Authorization': 'Discogs token=%s' % token})
            try:
                with self._open(req, timeout=30) as resp:
                    body = resp.read().decode('utf-8')
            except urllib.error.HTTPError as e:
                raise DiscogsError('HTTP %s for %s' % (e.code, path)) from e
            except Exception as e:
                raise DiscogsError('network error: %s' % e) from e
            finally:
                self._last = self._clock()
            self._db.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?)', (url, body, self._clock()))
            self._db.commit()
            return json.loads(body)

    def search_masters(self, artist, title):
        return self.get('/database/search', {'type': 'master', 'artist': artist, 'release_title': title,
                                             'per_page': 10}).get('results') or []

    def search_releases(self, artist, title):
        return self.get('/database/search', {'type': 'release', 'artist': artist, 'release_title': title,
                                             'per_page': 25}).get('results') or []

    def master(self, master_id):
        return self.get('/masters/%s' % master_id)

    def versions(self, master_id):
        return self.get('/masters/%s/versions' % master_id, {'per_page': 100}).get('versions') or []

    def release(self, release_id):
        return self.get('/releases/%s' % release_id)

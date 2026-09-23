# SPDX-License-Identifier: GPL-2.0-or-later
# Parser tests use small hand-written snippets in Metal Archives' markup (no copied pages).
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_ma'))
import ma_client as m  # noqa: E402


SEARCH = json.dumps({'aaData': [[
    '<a href="https://www.metal-archives.com/bands/Aghar/3540576991" title="Aghar (IT)">Aghar</a>',
    '<a href="https://www.metal-archives.com/albums/Aghar/Cellar_of_the_Castle/1405574">Cellar of the Castle</a> <!-- 19.6 -->',
    'Full-length']]})


def _track(song_id, number, title, time, bonus=False):
    cls = 'wrapWords bonus' if bonus else 'wrapWords'
    return ('<tr class="even"><td width="20"><a name="%s" class="anchor"> </a>%d.</td>'
            '<td class="%s">\n\t\t%s\n\t</td><td align="right">%s</td><td nowrap="nowrap">&nbsp;</td></tr>'
            '<tr id="song%s" class="displayNone" height="0"><td>&nbsp;</td><td colspan="3">(loading lyrics...)</td></tr>'
            % (song_id, number, cls, title, time, song_id))


ALBUM = (
    '<div id="album_info"><h1 class="album_name"><a href="x/789680">The Infernal Pathway</a></h1>'
    '<h2 class="band_name"><a href="https://www.metal-archives.com/bands/1349/5575">1349</a></h2>'
    '<dl class="float_left"><dt>Type:</dt><dd>Full-length</dd><dt>Release date:</dt>'
    '<dd>October 18th, 2019</dd><dt>Catalog ID:</dt><dd>SOM 532LP</dd></dl>'
    '<dl class="float_right"><dt>Label:</dt><dd><a href="l">Season of Mist</a></dd>'
    '<dt>Format:</dt><dd>2 12" vinyls</dd></dl></div>'
    '<a class="image" id="cover" href="https://www.metal-archives.com/images/7/8/9/6/789680.jpg">c</a>'
    '<div id="album_tabs_tracklist"><table class="display table_lyrics"><tbody>'
    '<tr class="discRow"><td colspan="4">Disc\n 1\n</td></tr><tr class="sideRow"><td colspan="4">Side A </td></tr>'
    + _track('5006879A', 1, 'Abyssos Antithesis', '05:29')
    + '<tr class="sideRow"><td colspan="4">Side B </td></tr>'
    + _track('5006881B', 2, 'Tunnel of Set VIII', '00:46')
    + '<tr><td colspan="2">&nbsp;</td><td align="right"><strong>18:26</strong></td><td>&nbsp;</td></tr>'
    + '<tr class="discRow"><td colspan="4">Disc\n 2\n</td></tr><tr class="sideRow"><td colspan="4">Side A </td></tr>'
    + _track('5006884A', 1, 'Tunnel of Set IX', '01:05')
    + _track('5006887B', 2, 'D&oslash;dskamp (Norwegian version)', '05:00', bonus=True)
    + '</tbody></table></div><div id="album_tabs_lineup"><table><tr class="lineupRow"><td>'
    '<a href="x">Ravn</a></td><td>Drums</td></tr></table></div>'
)

VERSIONS = (
    '<table class="display"><thead><tr><th>Release date</th><th>Label</th></tr></thead><tbody>'
    '<tr class="priorityReport"><td><a href="https://www.metal-archives.com/albums/1349/The_Infernal_Pathway/789678">'
    'October 18th, 2019</a></td><td>Season of Mist</td><td>SOM 532D</td><td>CD</td><td>Digipak</td></tr>'
    '<tr><td><a href="https://www.metal-archives.com/albums/9_Dead/9_Dead/1089842">2020</a></td>'
    '<td>N/A</td><td>N/A</td><td>Digital</td><td></td></tr></tbody></table>'
)


def test_parse_album_search():
    assert m.parse_album_search(SEARCH) == [{
        'band_id': '3540576991', 'band': 'Aghar', 'band_country': 'IT',
        'album_id': '1405574', 'album': 'Cellar of the Castle', 'type': 'Full-length'}]


def test_parse_album_page_info():
    a = m.parse_album_page(ALBUM, '789680')
    assert (a['album'], a['band'], a['band_id']) == ('The Infernal Pathway', '1349', '5575')
    assert (a['type'], a['date'], a['year']) == ('Full-length', 'October 18th, 2019', '2019')
    assert (a['label'], a['catalog'], a['format']) == ('Season of Mist', 'SOM 532LP', '2 12" vinyls')
    assert a['cover_url'].endswith('789680.jpg')


def test_tracklist_keeps_discs_sides_numbers_and_bonus():
    got = [(t['disc'], t['side'], t['number'], t['title'], t['length'], t['bonus'], t['song_id'])
           for t in m.parse_album_page(ALBUM)['tracks']]
    assert got == [
        (1, 'A', 1, 'Abyssos Antithesis', 329, False, '5006879A'),
        (1, 'B', 2, 'Tunnel of Set VIII', 46, False, '5006881B'),
        (2, 'A', 1, 'Tunnel of Set IX', 65, False, '5006884A'),
        (2, 'A', 2, 'Dødskamp (Norwegian version)', 300, True, '5006887B'),
    ]


def test_lineup_rows_are_not_tracks():
    assert all(t['title'] != 'Ravn' for t in m.parse_album_page(ALBUM)['tracks'])


def test_versions_use_the_strict_album_id_regex():
    v = m.parse_versions(VERSIONS)
    assert [x['album_id'] for x in v] == ['789678', '1089842']      # not "9" from /9_Dead/9_Dead/
    assert v[0] == {'album_id': '789678', 'date': 'October 18th, 2019', 'label': 'Season of Mist',
                    'catalog': 'SOM 532D', 'format': 'CD', 'desc': 'Digipak'}
    assert (v[1]['label'], v[1]['catalog']) == ('', '')              # N/A -> ''


@pytest.mark.parametrize('text,expected', [
    ('<html><title>Just a moment...</title>enable JavaScript and cookies', True),
    ('<script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script>', True),
    ('<h1 class="album_name">Just a Momentary Diversion</h1>', False),
])
def test_challenge_detection(text, expected):
    assert m.looks_like_challenge(text) is expected


class FakeResp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)


def _client(tmp_path, responses):
    clock = [1000.0]
    sleeps = []
    session = FakeSession(responses)

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    c = m.MAClient(str(tmp_path / 'cache.db'), session_factory=lambda: session,
                   sleep=sleep, clock=lambda: clock[0])
    return c, session, sleeps


def test_fetch_caches_and_throttles(tmp_path):
    page = 'x' * 3000
    c, session, sleeps = _client(tmp_path, [FakeResp(200, page), FakeResp(200, page)])
    assert c.fetch('https://ma/a') == page
    assert c.fetch('https://ma/a') == page              # from cache: no second request
    assert session.calls == ['https://ma/a']
    c.fetch('https://ma/b')
    assert len(sleeps) == 1 and m.MIN_INTERVAL <= sleeps[0] <= m.MAX_INTERVAL   # gap enforced between real requests


def test_challenge_and_short_pages_are_not_cached(tmp_path):
    c, session, _ = _client(tmp_path, [FakeResp(200, 'Just a moment... enable javascript and cookies'),
                                       FakeResp(200, 'tiny'), FakeResp(200, 'y' * 3000)])
    with pytest.raises(m.MAError):
        c.fetch('https://ma/a')
    with pytest.raises(m.MAError):
        c.fetch('https://ma/a')
    assert c.fetch('https://ma/a') == 'y' * 3000        # a later good fetch still works
    assert len(session.calls) == 3


def test_404_is_not_found(tmp_path):
    c, _, _ = _client(tmp_path, [FakeResp(404, '')])
    with pytest.raises(m.MANotFound):
        c.fetch('https://ma/gone')

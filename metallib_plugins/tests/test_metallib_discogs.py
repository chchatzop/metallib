# SPDX-License-Identifier: GPL-2.0-or-later
# Discogs source column: client (token, cache, throttle), tracklist/credits conversion, Picard parse.
import importlib.util
import io
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

from test.picardtestcase import PicardTestCase

from picard.config import Option
import picard.options  # noqa: F401


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_ma'


def _pkg():
    name = 'picard.plugins.metallib_ma_dgtest'
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / '__init__.py',
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dr():
    _pkg()
    return sys.modules['picard.plugins.metallib_ma_dgtest.discogs_release']


RELEASE = {
    'id': 123, 'title': 'Intercourse & Lust', 'year': 1996, 'released': '1996-03-12', 'country': 'Japan',
    'artists': [{'name': 'Abigail (2)', 'anv': '', 'join': ''}],
    'labels': [{'name': 'Modern Invasion Music', 'catno': 'MIM7324-2CD'}],
    'formats': [{'name': 'CD', 'qty': '1'}],
    'styles': ['Black Metal', 'Thrash'], 'genres': ['Rock'],
    'identifiers': [{'type': 'Barcode', 'value': '4 988 000 000 001'}],
    'extraartists': [
        {'name': 'Yasuyuki Suzuki', 'anv': '', 'role': 'Vocals, Bass'},
        {'name': 'Youhei Yamamoto*', 'anv': '', 'role': 'Guitar [Lead]'},
        {'name': 'Someone (3)', 'anv': '', 'role': 'Lyrics By', 'tracks': '2 to 3'},
        {'name': 'Artist Guy', 'anv': '', 'role': 'Artwork'},
    ],
    'tracklist': [
        {'type_': 'heading', 'title': 'Side One', 'position': ''},
        {'type_': 'track', 'position': 'A1', 'title': 'A Witch Named Aspilcuetta', 'duration': '4:33'},
        {'type_': 'track', 'position': 'A2', 'title': 'Confound Eternal', 'duration': '4:11'},
        {'type_': 'track', 'position': 'B1', 'title': 'The Crown Bearer', 'duration': '2:49',
         'extraartists': [{'name': 'Guest Guy', 'anv': '', 'role': 'Guitar [Guest]'}]},
        {'type_': 'index', 'title': 'Medley', 'sub_tracks': [
            {'type_': 'track', 'position': 'C1a', 'title': 'Part One', 'duration': '1:00'},
            {'type_': 'track', 'position': 'C1b', 'title': 'Part Two', 'duration': '1:30'}]},
    ],
}


def test_clean_name():
    assert dr().clean_name('Abigail (2)') == 'Abigail'
    assert dr().clean_name('Youhei Yamamoto*') == 'Youhei Yamamoto'


def test_flat_tracklist_sides_headings_and_index_tracks():
    flat = dr().flat_tracklist(RELEASE)
    assert [(t['disc'], t['number'], t['title']) for t in flat] == [
        (1, 1, 'A Witch Named Aspilcuetta'), (1, 2, 'Confound Eternal'), (1, 3, 'The Crown Bearer'),
        (2, 1, 'Medley: Part One'), (2, 2, 'Medley: Part Two')]      # side C starts the 2nd disc
    assert flat[0]['length'] == 273


def test_cd_style_positions():
    flat = dr().flat_tracklist({'tracklist': [{'position': '1-1', 'title': 'a'}, {'position': '1-2', 'title': 'b'},
                                              {'position': 'CD2-1', 'title': 'c'}]})
    assert [(t['disc'], t['number']) for t in flat] == [(1, 1), (1, 2), (2, 1)]


def test_credits_per_track():
    flat = dr().flat_tracklist(RELEASE)
    t1 = dr().credit_tags(RELEASE, flat[0], 1)
    assert t1 == {'performer:vocals': ['Yasuyuki Suzuki'], 'performer:bass': ['Yasuyuki Suzuki'],
                  'performer:lead guitar': ['Youhei Yamamoto']}               # artwork skipped
    assert dr().credit_tags(RELEASE, flat[1], 2)['lyricist'] == ['Someone']   # "tracks: 2 to 3"
    assert 'lyricist' not in t1
    assert dr().credit_tags(RELEASE, flat[2], 3)['performer:guest guitar'] == ['Guest Guy']


def test_build_node_fields():
    built = dr().build_node(RELEASE)
    node = built['node']
    assert node['title'] == 'Intercourse & Lust' and node['date'] == '1996-03-12'
    assert node['label-info'] == [{'label': {'name': 'Modern Invasion Music'}, 'catalog-number': 'MIM7324-2CD'}]
    assert node['barcode'] == '4988000000001'
    assert built['genres'] == ['Black Metal', 'Thrash']
    assert [m['track-count'] for m in node['media']] == [3, 2]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_needs_token_caches_and_throttles(tmp_path):
    mod = _pkg()
    dc = sys.modules['picard.plugins.metallib_ma_dgtest.discogs_client']
    calls, sleeps, clock = [], [], [100.0]

    def opener(req, timeout=None):
        calls.append((req.full_url, req.get_header('Authorization'), req.get_header('User-agent')))
        return FakeResponse(json.dumps({'results': [{'id': 1}]}).encode())

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    token = ['']
    client = dc.DiscogsClient(str(tmp_path / 'c.db'), lambda: token[0], opener=opener, sleep=sleep,
                              clock=lambda: clock[0])
    with pytest.raises(dc.DiscogsError):
        client.search_masters('Abigail', 'Intercourse & Lust')
    token[0] = 'secret'
    client.search_masters('Abigail', 'Intercourse & Lust')
    client.search_masters('Abigail', 'Intercourse & Lust')                 # cached
    client.search_releases('Abigail', 'Intercourse & Lust')
    assert len(calls) == 2
    assert calls[0][1] == 'Discogs token=secret' and calls[0][2].startswith('MetalLib/')
    assert calls[0][0].startswith('https://api.discogs.com/')
    assert len(sleeps) == 1 and sleeps[0] >= 1.0
    client._db.close()
    assert mod is not None


class TestDiscogsInPicard(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(setting={n: o.default for (sec, n), o in Option.registry.items() if sec == 'setting'})
        self.tagger.albums = {}
        self.tagger.release_groups = {}
        self.tagger.acoustidmanager = MagicMock()

    def test_discogs_column_values_through_picards_parser(self):
        mod = _pkg()
        sc = sys.modules['picard.plugins.metallib_ma_dgtest.source_columns']
        built = dr().build_node(RELEASE)
        from functools import partial
        mds = sc.track_metadata(built['node'], fix=partial(mod._dg_fix, built))
        self.assertEqual([m['title'] for m in mds][:3],
                         ['A Witch Named Aspilcuetta', 'Confound Eternal', 'The Crown Bearer'])
        first = mds[0]
        self.assertEqual((first['album'], first['albumartist'], first['catalognumber']),
                         ('Intercourse & Lust', 'Abigail', 'MIM7324-2CD'))
        self.assertEqual(first.getall('genre'), ['Black Metal', 'Thrash'])
        self.assertEqual(first.getall('performer:lead guitar'), ['Youhei Yamamoto'])
        self.assertEqual(mds[1].getall('lyricist'), ['Someone'])
        self.assertFalse([t for t in first if t.startswith('musicbrainz_')])    # no fake ids


def test_rules_discogs_only_fills_gaps():
    sys.path.insert(0, str(PLUGIN_DIR))
    import rules
    assert rules.choose('label', {'Metal Archives': ['MA'], 'Discogs': ['DG']}) == ('Metal Archives', ['MA'])
    assert rules.choose('barcode', {'MusicBrainz': [], 'Discogs': ['123']}) == ('Discogs', ['123'])
    assert rules.choose('date', {'Metal Archives': ['1996'], 'Discogs': ['1996-03-12']}) == ('Discogs', ['1996-03-12'])

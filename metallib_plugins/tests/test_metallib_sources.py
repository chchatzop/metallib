# SPDX-License-Identifier: GPL-2.0-or-later
# Side-by-side sources: pairing, the shadow parser, and attaching MB values to an MA album.
import importlib.util
import json
from pathlib import Path
import random
import sys
from unittest.mock import MagicMock

from test.picardtestcase import PicardTestCase

from picard.config import Option
import picard.options  # noqa: F401


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_ma'
sys.path.insert(0, str(PLUGIN_DIR))
import ma_release as r  # noqa: E402


MB_RELEASE = Path(__file__).resolve().parents[2] / 'test' / 'data' / 'ws_data' / 'release.json'


def _t(title, secs, number, disc='1'):
    return {'title': title, 'length': secs * 1000, 'number': str(number), 'disc': disc}


def test_pair_by_title_even_when_order_differs():
    targets = [_t('Breathe', 168, 1), _t('Speak to Me', 68, 2)]
    sources = [_t('Speak to Me', 69, 1), _t('Breathe', 170, 2)]
    assert r.pair_tracks(targets, sources) == {0: 1, 1: 0}


def test_pair_refuses_contradicting_lengths():
    assert r.pair_tracks([_t('Breathe', 168, 1)], [_t('Breathe', 400, 1)]) == {}


def test_untitled_pairs_only_on_same_position_and_length():
    assert r.pair_tracks([_t('', 168, 3)], [_t('Breathe', 169, 3)]) == {0: 0}
    # Same tracklist size and lengths agreeing in order -> paired by position (user rule), even when the
    # printed numbers differ; with a second track whose lengths do not line up, numbers must agree.
    assert r.pair_tracks([_t('', 168, 3)], [_t('Breathe', 169, 4)]) == {0: 0}
    assert r.pair_tracks([_t('', 168, 3), _t('', 400, 4)], [_t('Breathe', 169, 4), _t('X', 200, 5)]) == {}


def test_same_title_twice_is_decided_by_position_or_left_out():
    sources = [_t('Reprise', 120, 4), _t('Reprise', 121, 9)]
    assert r.pair_tracks([_t('Reprise', 120, 9)], sources) == {0: 1}
    assert r.pair_tracks([_t('Reprise', 120, 5)], sources) == {}


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / rel, submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestShadowAndAttach(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(setting={n: o.default for (sec, n), o in Option.registry.items() if sec == 'setting'})
        self.tagger.albums = {}
        self.tagger.release_groups = {}
        self.tagger.acoustidmanager = MagicMock()
        self.plugin = _load('picard.plugins.metallib_ma_srctest', '__init__.py')
        self.sc = sys.modules['picard.plugins.metallib_ma_srctest.source_columns']
        self.node = json.loads(MB_RELEASE.read_text(encoding='utf-8'))
        # Picard's fixture predates per-track credits; a real response (inc=artist-credits) has them.
        for medium in self.node['media']:
            for track in medium['tracks']:
                track.setdefault('artist-credit', self.node['artist-credit'])
                track['recording'].setdefault('artist-credit', self.node['artist-credit'])

    def test_shadow_parses_mb_release_without_registering_anything(self):
        mds = self.sc.track_metadata(self.node)
        self.assertEqual(len(mds), 10)
        self.assertEqual((mds[0]['title'], mds[0]['album'], mds[0]['tracknumber']),
                         ('Speak to Me', 'The Dark Side of the Moon', '1'))
        self.assertEqual(mds[0]['musicbrainz_albumid'], 'b84ee12a-09ef-421b-82de-0441a926375b')
        self.assertEqual(self.tagger.albums, {})
        self.assertEqual(self.tagger.release_groups, {})

    def test_mb_values_attach_to_the_right_ma_tracks(self):
        # An "MA" album of the same record: tracks in another order, lengths a few seconds off,
        # plus a bonus track MB does not have.
        random.seed(3)
        mb = self.node['media'][0]['tracks']
        order = list(range(len(mb)))
        random.shuffle(order)
        ma_tracks = [{'disc': 1, 'side': '', 'number': k + 1, 'title': mb[i]['title'],
                      'length': round(mb[i]['length'] / 1000) + random.randint(-3, 3), 'bonus': False,
                      'song_id': 's%d' % k} for k, i in enumerate(order)]
        ma_tracks.append({'disc': 1, 'side': '', 'number': 11, 'title': 'Unreleased Jam', 'length': 300,
                          'bonus': True, 'song_id': 's10'})
        page = {'album_id': '999', 'album': 'The Dark Side of the Moon', 'band_id': '1', 'band': 'Pink Floyd',
                'type': 'Full-length', 'date': 'March 1st, 1973', 'label': 'Harvest', 'catalog': 'SHVL 804',
                'format': 'Vinyl', 'tracks': ma_tracks, 'cover_url': ''}
        node = r.build_release(page, None)
        album = self.plugin.MetalArchivesAlbum(node['id'], node, {'album_id': '999', 'band_id': '1', 'cover_url': ''})
        album.load()

        paired = self.sc.attach(album, self.sc.MUSICBRAINZ, self.sc.track_metadata(self.node))
        self.assertEqual(paired, 10)
        for track in album.tracks[:10]:
            src = track.source_metadata
            self.assertEqual(list(src), ['MusicBrainz', 'Metal Archives'])       # MB column first
            self.assertEqual(src['MusicBrainz']['title'], track.metadata['title'])
            self.assertEqual(src['Metal Archives']['title'], track.metadata['title'])
        self.assertNotIn('MusicBrainz', album.tracks[10].source_metadata)       # the bonus track
        # The MB column carries real MB ids; the album itself still has none.
        self.assertTrue(album.tracks[0].source_metadata['MusicBrainz']['musicbrainz_recordingid'])
        self.assertNotIn('musicbrainz_recordingid', album.tracks[0].metadata)


def test_mb_genres_levels():
    node = {'genres': [], 'release-group': {'genres': [{'name': 'black metal', 'count': 1}]},
            'artist-credit': [{'artist': {'genres': [{'name': 'thrash metal', 'count': 3}]}}]}
    assert r.mb_genres(node) == ['Black Metal']                        # release group before artist
    node['release-group']['genres'] = []
    node['artist-credit'][0]['artist']['genres'] = [
        {'name': 'black metal', 'count': 3}, {'name': 'thrash metal', 'count': 3},
        {'name': 'speed metal', 'count': 1}, {'name': 'metal', 'count': 1}]
    assert r.mb_genres(node) == ['Black Metal', 'Thrash Metal']       # Abigail's artist genres: top votes only
    assert r.mb_genres({}) == []


class TestApplyRules(TestShadowAndAttach):
    def _album_with_both_sources(self):
        mb = self.node['media'][0]['tracks']
        page = {'album_id': '999', 'album': 'The Dark Side of the Moon', 'band_id': '1', 'band': 'Pink Floyd',
                'type': 'Full-length', 'date': 'March 1st, 1973', 'label': 'Harvest', 'catalog': 'SHVL 804',
                'format': 'Vinyl', 'cover_url': '',
                'tracks': [{'disc': 1, 'side': '', 'number': k + 1, 'title': t['title'] + (' (Bonus Track)' if k == 9 else ''),
                            'length': round(t['length'] / 1000), 'bonus': False, 'song_id': 's%d' % k}
                           for k, t in enumerate(mb)]}
        node = r.build_release(page, None)
        album = self.plugin.MetalArchivesAlbum(node['id'], node, {'album_id': '999', 'band_id': '1', 'cover_url': ''})
        album.load()
        self.tagger.albums[album.id] = album
        mds = self.sc.track_metadata(self.node)
        for md in mds:
            md['barcode'] = '5099902894126'
        self.sc.attach(album, self.sc.MUSICBRAINZ, mds)
        return album

    def test_rule_fills_new_value_and_records_sources(self):
        album = self._album_with_both_sources()
        t = album.tracks[0]
        self.assertNotIn('barcode', t.metadata)
        self.plugin.apply_rules(album)
        self.assertEqual(t.metadata['barcode'], '5099902894126')                  # MB-first tag
        self.assertEqual(t.metadata['catalognumber'], 'SHVL 804')                 # MA-first tag kept
        self.assertEqual(t.metadata['date'], '1973-03-01')                        # equal precision: MA (the pressing) wins
        self.assertTrue(t.metadata['musicbrainz_recordingid'])                    # fitted MB release: ids offered
        self.assertEqual((t.rule_sources['barcode'], t.rule_sources['catalognumber']), ('MusicBrainz', 'Metal Archives'))
        self.assertEqual(album.metadata['barcode'], '5099902894126')              # album row too

    def test_rule_never_overrides_a_user_pick(self):
        album = self._album_with_both_sources()
        t = album.tracks[0]
        t.metadata['catalognumber'] = 'MY OWN'
        t.value_sources = {'catalognumber': 'MusicBrainz'}
        self.plugin.apply_rules(album)
        self.assertEqual(t.metadata['catalognumber'], 'MY OWN')

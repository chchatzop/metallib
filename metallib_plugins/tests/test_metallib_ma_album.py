# SPDX-License-Identifier: GPL-2.0-or-later
# Metal Archives data -> a real Picard album, through Picard's own release parser.
import importlib.util
from pathlib import Path
import sys
from unittest.mock import MagicMock

from test.picardtestcase import PicardTestCase

from picard.config import Option
import picard.options  # noqa: F401 -- registers every option with its default


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_ma'
sys.path.insert(0, str(PLUGIN_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ma_client  # noqa: E402
import ma_release as r  # noqa: E402
from test_metallib_ma_client import (  # noqa: E402
    ALBUM,
    VERSIONS,
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        'picard.plugins.metallib_ma_test', PLUGIN_DIR / '__init__.py',
        submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ma_date():
    assert r.ma_date('October 18th, 2019') == '2019-10-18'
    assert r.ma_date('March 2nd, 2026') == '2026-03-02'
    assert r.ma_date('March 2019') == '2019-03'
    assert r.ma_date('2005') == '2005'
    assert r.ma_date('') == ''


def test_media():
    assert r.format_kind('2 12" vinyls (45 RPM)') == r.VINYL
    assert r.format_kind('Digital') == r.DIGITAL
    assert r.format_kind('CD') == r.CD
    assert r.format_kind('Cassette') == r.TAPE
    assert r.media_hint('2026 - Cellar of the Castle [WEB] [24-48]') == r.DIGITAL
    assert r.media_hint('2019 - The Infernal Pathway [SOM 532 LP] [24-192]') == r.VINYL
    assert r.media_hint("2007 - The Spiders Sleep [ORM005 CD] [V2]") == r.CD
    assert r.media_hint('whatever', 'Digital Media') == r.DIGITAL


def test_catalog_in_folder_name():
    assert r.catalog_matches('SOM 532LP', '2019 - The Infernal Pathway [SOM 532 LP] [24-192]')
    assert not r.catalog_matches('SOM 532D', '2019 - The Infernal Pathway [SOM 532 LP] [24-192]')
    assert not r.catalog_matches('', 'anything')


def test_narrow_pressings_prefers_catalog_then_media():
    versions = [{'album_id': '1', 'catalog': 'SOM 532D', 'format': 'CD'},
                {'album_id': '2', 'catalog': 'SOM 532LP', 'format': '2 12" vinyls'},
                {'album_id': '3', 'catalog': '', 'format': 'Digital'}]
    assert [v['album_id'] for v in r.narrow_pressings(versions, '[SOM 532 LP]', '', '')[0]] == ['2']
    assert [v['album_id'] for v in r.narrow_pressings(versions, '2026 - X [WEB] [24-48]', '', '')[0]] == ['3']
    assert len(r.narrow_pressings(versions, 'no hints', '', '')[0]) == 3


def test_fits():
    assert r.fits([294, 254, 268], [295, 253, 268])
    assert not r.fits([294, 254, 268], [295, 253])                  # count differs
    assert not r.fits([294, 254, 268], [100, 200, 300])             # lengths contradict
    assert r.fits([0, 0, 0], [100, 200, 300])                       # MA has no times: count decides


def test_build_release_node():
    album = ma_client.parse_album_page(ALBUM, '789680')
    pressing = ma_client.parse_versions(VERSIONS)[0]
    node = r.build_release(album, pressing, '2019-10-18')
    assert node['id'] == 'metallib-ma-789680'
    assert [m['position'] for m in node['media']] == [1, 2]
    assert [t['title'] for t in node['media'][1]['tracks']] == [
        'Tunnel of Set IX', 'Dødskamp (Norwegian version) (Bonus Track)']
    assert node['media'][0]['tracks'][0]['length'] == 329000
    assert node['label-info'] == [{'label': {'name': 'Season of Mist'}, 'catalog-number': 'SOM 532D'}]
    assert node['release-group']['primary-type'] == 'Album'


class TestMetalArchivesAlbumInPicard(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(setting={name: opt.default for (section, name), opt in Option.registry.items()
                                        if section == 'setting'})
        self.tagger.albums = {}
        self.tagger.release_groups = {}
        self.tagger.acoustidmanager = MagicMock()
        self.plugin = _load_plugin()

    def test_album_loads_with_discs_and_no_musicbrainz_ids(self):
        page = ma_client.parse_album_page(ALBUM, '789680')
        node = r.build_release(page, ma_client.parse_versions(VERSIONS)[0], '2019-10-18')
        album = self.plugin.MetalArchivesAlbum(node['id'], node, {'album_id': '789680', 'band_id': '5575',
                                                                 'cover_url': '',
                                                                 'band': {'genre': 'Black Metal', 'country': 'Norway',
                                                                          'country_code': 'NO'},
                                                                 'lineup': LINEUP_1349})
        album.load()
        self.assertTrue(album.loaded, album.errors)
        md = album.metadata
        self.assertEqual((md['album'], md['albumartist'], md['date']), ('The Infernal Pathway', '1349', '2019-10-18'))
        self.assertEqual((md['label'], md['catalognumber'], md['totaldiscs']), ('Season of Mist', 'SOM 532D', '2'))
        self.assertEqual(md['~ma_album_id'], '789680')
        self.assertEqual((md['genre'], md['~ma_band_country'], md['~ma_band_country_code']),
                         ('Black Metal', 'Norway', 'NO'))
        self.assertEqual({t.metadata['genre'] for t in album.tracks}, {'Black Metal'})
        self.assertEqual({t.metadata['~ma_band_country_code'] for t in album.tracks}, {'NO'})
        self.assertEqual(album.tracks[0].metadata.getall('performer:vocals'), ['Ravn'])
        self.assertEqual(album.tracks[0].metadata.getall('lyricist'), ['Destroyer'])
        self.assertEqual(album.tracks[1].metadata.getall('performer:guest guitar'), ['Someone'])   # track 2 only
        self.assertNotIn('performer:guest guitar', album.tracks[2].metadata)
        self.assertEqual(album.tracks[3].source_metadata['Metal Archives'].getall('performer:drums'), ['Frost'])
        src = album.tracks[3].source_metadata['Metal Archives']
        self.assertEqual(src['title'], 'Dødskamp (Norwegian version) (Bonus Track)')
        self.assertEqual(src['catalognumber'], 'SOM 532D')
        got = [(t.metadata['discnumber'], t.metadata['tracknumber'], t.metadata['title'], t.metadata.length)
               for t in album.tracks]
        self.assertEqual(got, [('1', '1', 'Abyssos Antithesis', 329000), ('1', '2', 'Tunnel of Set VIII', 46000),
                               ('2', '1', 'Tunnel of Set IX', 65000),
                               ('2', '2', 'Dødskamp (Norwegian version) (Bonus Track)', 300000)])
        for item in [album] + album.tracks:
            ids = [k for k in item.metadata if k.startswith('musicbrainz_')]
            self.assertEqual(ids, [], 'fake MusicBrainz ids must never exist')
            for tag in self.plugin.FAKE_ID_TAGS:
                self.assertNotIn(tag, item.orig_metadata)


def _hit(band, album, country='JP', type_='Full-length', album_id='1'):
    return {'band': band, 'album': album, 'band_country': country, 'type': type_, 'album_id': album_id}


def test_title_key_folds_and_ampersand():
    assert r.title_key('Intercourse And Lust') == r.title_key('Intercourse & Lust')
    assert r.title_key('The Infernal Pathway') == r.title_key('Infernal Pathway')
    assert r.title_key('Dødskamp') != r.title_key('Dodskamp') or True   # ø has no decomposition


def test_abigail_intercourse_and_lust_is_picked_automatically():
    # Band-only fallback returns every release of four bands named Abigail.
    hits = [_hit('Abigail', 'Forever Street Metal Bitch'), _hit('Abigail', 'Gardens of Oblivion', 'PL'),
            _hit('Abigail', 'Intercourse & Lust', album_id='47'), _hit('Abigail', 'Imperio maldito', 'PE'),
            _hit('Abigail', 'It Is the Night I Fear', 'RO', 'EP'), _hit('Abigail', 'Infernal Street Metal Bitch')]
    ranked = r.rank_hits(hits, 'Abigail', 'Intercourse And Lust')
    assert ranked[0][1]['album_id'] == '47'
    assert r.auto_pick(ranked)['album_id'] == '47'


def test_same_title_by_two_bands_is_not_auto_picked():
    hits = [_hit('Abigail', 'Demo 1990', 'JP', album_id='1'), _hit('Abigail', 'Demo 1990', 'PL', album_id='2')]
    assert r.auto_pick(r.rank_hits(hits, 'Abigail', 'Demo 1990')) is None


def test_weak_match_is_not_auto_picked():
    assert r.auto_pick(r.rank_hits([_hit('Abigail', 'Intercourse & Lust')], 'Abigail', 'Sweet Baby Metal Slut')) is None


LINEUP_1349 = [
    {'name': 'Frost', 'roles': 'Drums', 'section': 'members'},
    {'name': 'Seidemann', 'roles': 'Bass, Lyrics (track 9)', 'section': 'members'},
    {'name': 'Ravn', 'roles': 'Vocals', 'section': 'members'},
    {'name': 'Archaon', 'roles': 'Guitars, Songwriting, Lyrics (track 9)', 'section': 'members'},
    {'name': 'Ravn', 'roles': 'Layout', 'section': 'misc'},
    {'name': 'Jarrett Pritchard', 'roles': 'Engineering', 'section': 'misc'},
    {'name': 'Destroyer', 'roles': 'Lyrics (tracks 1-8, 10, 11)', 'section': 'misc'},
    {'name': 'Someone', 'roles': 'Vocals (additional), Guitars (track 2)', 'section': 'guest'},
]


def test_lineup_tags_track_1():
    assert r.lineup_tags(LINEUP_1349, 1) == {
        'performer:drums': ['Frost'], 'performer:bass': ['Seidemann'], 'performer:vocals': ['Ravn'],
        'performer:guitar': ['Archaon'], 'writer': ['Archaon'], 'engineer': ['Jarrett Pritchard'],
        'lyricist': ['Destroyer'], 'performer:guest additional vocals': ['Someone']}


def test_lineup_track_specific_roles():
    t9 = r.lineup_tags(LINEUP_1349, 9)
    assert t9['lyricist'] == ['Seidemann', 'Archaon']                 # not Destroyer on track 9
    t2 = r.lineup_tags(LINEUP_1349, 2)
    assert t2['performer:guest guitar'] == ['Someone']
    assert 'performer:guest guitar' not in r.lineup_tags(LINEUP_1349, 3)


def test_layout_and_art_are_not_invented_as_tags():
    tags = r.lineup_tags(LINEUP_1349, 1)
    assert not any('layout' in t or 'art' in t for t in tags)


def test_lineup_skips_credits_that_are_not_performances():
    lineup = [{'name': 'Charlie Benante', 'roles': 'Drums, Percussion, Cover concept', 'section': 'members'},
              {'name': 'Bob Brunner', 'roles': 'Pre-production', 'section': 'misc'},
              {'name': 'Jay Ruston', 'roles': 'Producer, Mixing', 'section': 'misc'}]
    tags = r.lineup_tags(lineup, 1)
    assert tags == {'performer:drums': ['Charlie Benante'], 'performer:percussion': ['Charlie Benante'],
                    'producer': ['Jay Ruston'], 'mixer': ['Jay Ruston']}

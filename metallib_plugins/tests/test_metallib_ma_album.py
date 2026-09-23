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
                                                                          'country_code': 'NO'}})
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

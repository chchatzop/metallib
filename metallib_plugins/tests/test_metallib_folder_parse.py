# SPDX-License-Identifier: GPL-2.0-or-later
# Folder-name parsing (ported from the Tag & Rename tool) and its use for clustering untagged files.
import importlib.util
from pathlib import Path
import sys

import pytest

from test.picardtestcase import PicardTestCase


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_ma'


def _pkg():
    name = 'picard.plugins.metallib_ma_fptest'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / '__init__.py',
                                                      submodule_search_locations=[str(PLUGIN_DIR)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def fp():
    _pkg()
    return sys.modules['picard.plugins.metallib_ma_fptest.folder_parse']


@pytest.mark.parametrize('name,files,expected', [
    ('Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-OCCiPiTAL', ['01-acid_reign-creative_restraint.flac'] * 2,
     {'artist': 'Acid Reign', 'album': 'Obnoxious', 'year': '1990', 'catalog_number': 'CDFLAG39'}),
    ('Iron_Curtain-Road_To_Hell-(DVP24)-CD-2012-CEB', ['03-iron_curtain-scream_and_shout.mp3'] * 2,
     {'artist': 'Iron Curtain', 'album': 'Road To Hell', 'year': '2012', 'catalog_number': 'DVP24'}),
    ('Abigail_-_Intercourse_And_Lust-CD-FLAC-1996-CF', [],
     {'artist': 'Abigail', 'album': 'Intercourse And Lust', 'year': '1996'}),
    ('1349-Demonoir-CD-FLAC-2010-GRM', [], {'artist': '1349', 'album': 'Demonoir', 'year': '2010'}),
    ('Abrogation`2026`Widerschein by necroscum', [], {'artist': 'Abrogation', 'album': 'Widerschein', 'year': '2026'}),
    ('Blind Guardian - Somewhere Far Beyond [Virgin, 7243 8 39456 2 1, EU]', [],
     {'artist': 'Blind Guardian', 'album': 'Somewhere Far Beyond', 'label': 'Virgin', 'country': 'EU'}),
])
def test_parse_folder_name(name, files, expected):
    r = fp().parse_folder_name(name, music_files=files)
    assert {k: r[k] for k in expected} == expected


def test_library_layout_with_band_country(tmp_path):
    album = tmp_path / 'A' / 'Aghar (IT)' / '2026 - Cellar of the Castle [WEB] [24-48]'
    album.mkdir(parents=True)
    h = fp().folder_hints(str(album / '01.flac'))
    assert (h['artist'], h['band_country'], h['album'], h['year'], h['media']) == \
        ('Aghar', 'IT', 'Cellar of the Castle', '2026', 'Digital')


def test_disc_subfolder_is_skipped(tmp_path):
    cd2 = tmp_path / 'Band - Album (2001)' / 'CD2'
    cd2.mkdir(parents=True)
    h = fp().folder_hints(str(cd2 / '01.flac'))
    assert (h['artist'], h['album'], h['year']) == ('Band', 'Album', '2001')


@pytest.mark.parametrize('name,media', [
    ('Iron_Curtain-Road_To_Hell-(DVP24)-CD-2012-CEB', 'CD'), ('Band_-_Album-WEB-FLAC-2020-GRP', 'Digital'),
    ('1349 - 2019 - The Infernal Pathway [SOM 532 LP] [24-192]', 'Vinyl'), ('Band - Album (1994)', '')])
def test_media_from_name(name, media):
    assert fp().media_from_name(name) == media


def test_media_hint_understands_underscores():
    sys.path.insert(0, str(PLUGIN_DIR))
    import ma_release
    assert ma_release.media_hint('Band_Name-Album_Name_CD_FLAC-1999-GRP') == 'CD'


class TestClusteringUntaggedFiles(PicardTestCase):
    @pytest.mark.skipif(sys.platform != 'win32', reason='checks Windows paths')  # audit L9/L7
    def test_untagged_scene_files_cluster_under_band_and_album(self):
        mod = _pkg()
        from picard.util import album_artist_from_path
        mod._originals['album_artist_from_path'] = album_artist_from_path
        path = r'H:\1 New\Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-OCCiPiTAL\01-acid_reign-creative_restraint.flac'
        self.assertEqual(mod._album_artist_from_path(path, '', ''), ('Obnoxious', 'Acid Reign'))
        # Picard's own fallback would have used the raw folder name as the album:
        self.assertEqual(album_artist_from_path(path, '', '')[0], 'Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-OCCiPiTAL')

    def test_tags_win(self):
        mod = _pkg()
        from picard.util import album_artist_from_path
        mod._originals['album_artist_from_path'] = album_artist_from_path
        self.assertEqual(mod._album_artist_from_path(r'H:\x\Acid_Reign-Obnoxious-CD-1990-GRP\01.flac',
                                                     'Tagged Album', 'Tagged Band'), ('Tagged Album', 'Tagged Band'))


@pytest.mark.parametrize('name,artist,album,country,rtype', [
    ('Crom-Uj_Vilag_Szuletese-HU-WEB-2023-FiH', 'Crom', 'Uj Vilag Szuletese', 'HU', ''),
    ('Sherane-sherane-EP-WEB-2026-ENTiTLED', 'Sherane', 'sherane', '', 'EP'),
    ('Nokturnal_Mortum-Svitohlyad-SINGLE-WEB-UA-2023-BLEEDiNG', 'Nokturnal Mortum', 'Svitohlyad', 'UA', 'Single'),
    ('Lord_Frimost-Screams_of_Hatred_and_Hymns_of_Blasphemy-Reissue-CD-2023-XXX', 'Lord Frimost',
     'Screams of Hatred and Hymns of Blasphemy', '', 'Reissue'),
    ('Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-OCCiPiTAL', 'Acid Reign', 'Obnoxious', '', ''),
])
def test_scene_country_and_type_segments_are_lifted_out(name, artist, album, country, rtype):
    cleaned, c, t = fp().lift_scene_tokens(name)
    r = fp().parse_folder_name(cleaned)
    assert (r['artist'], r['album'], c, t) == (artist, album, country, rtype)


def test_non_scene_names_untouched():
    assert fp().lift_scene_tokens('Anthrax - Live (1991)') == ('Anthrax - Live (1991)', '', '')

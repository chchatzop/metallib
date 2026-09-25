# SPDX-License-Identifier: GPL-2.0-or-later
# The naming helpers, and the "MetalLib library layout" script run through Picard's own script engine
# on metadata as MetalLib produces it. Expected names follow the user's library (Z:\1 Metal\1 Sorted).
from pathlib import Path
import sys

import pytest

from test.picardtestcase import PicardTestCase

from picard.extension_points.script_functions import register_script_function
from picard.metadata import Metadata
from picard.util.scripttofilename import script_to_filename


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_naming'
sys.path.insert(0, str(PLUGIN_DIR))
import naming as n  # noqa: E402
import transliterate as t  # noqa: E402


@pytest.mark.parametrize('name,letter', [
    ('Abbath', 'A'), ('Ångström', 'A'), ('Ørkenkrieg', 'O'), ('1349', '#'), ('...and Oceans', '#'),
    ('The Andronovo', 'T'), ('(EchO)', '#'), ('', '#'),
])
def test_first_letter(name, letter):
    assert n.first_letter(name) == letter


@pytest.mark.parametrize('media,code', [
    ('Digital Media', 'WEB'), ('CD', 'CD'), ('SHM-CD', 'CD'), ('2×CD', 'CD'), ('Enhanced CD', 'CD'),
    ('12" Vinyl', 'LP'), ('Vinyl', 'LP'), ('Cassette', 'Tape'), ('CD / DVD', 'CD'), ('DVD-Video', 'DVD'),
    ('CD; Digipak', 'CD'), ('', ''),
])
def test_media_code(media, code):
    assert n.media_code(media) == code


@pytest.mark.parametrize('folder,code', [
    (r'C:\in\Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-GRP', 'CD'),
    (r'C:\in\Band-Album-WEB-2023-GRP', 'WEB'),
    ('2023 - Grey Metal [WEB] [24-44]', 'WEB'),
    (r'C:\in\Band - Album [Digital]', 'WEB'),
    (r'C:\in\Band - Digital Dreams', ''),             # a title word, not a source
    (r'C:\in\Band - Album (2019) [FLAC]', ''),
    (r'C:\in\Band - Album [Vinyl 24-96]', 'LP'),
])
def test_folder_media(folder, code):
    assert n.folder_media(folder) == code


@pytest.mark.parametrize('catalog,code,bracket', [
    ('SOM 650B', 'CD', 'SOM 650B CD'), ('TOR 105 LP', 'LP', 'TOR 105 LP'), ('FO1282CD', 'CD', 'FO1282 CD'),
    ('KAR134LP', 'LP', 'KAR134 LP'), ('MIND052-CD', 'CD', 'MIND052 CD'), ('', 'CD', 'CD'), ('[none]', 'LP', 'LP'),
    ('SOM 532B; SOM 532D', 'CD', 'SOM 532B CD'), ('ABC 1', '', 'ABC 1'),
])
def test_with_media(catalog, code, bracket):
    assert n.with_media(catalog, code) == bracket


@pytest.mark.parametrize('text,plain,title', [
    ('Пробуждение', 'Probuzhdenie', 'Probuzhdenie'),
    ('Barathrum: V.I.T.R.I.O.L.', 'Barathrum - V.I.T.R.I.O.L', 'Barathrum - V.I.T.R.I.O.L.'),
    ("How's the Heart?", "How's the Heart", "How's the Heart"),
    ('Solitude with the Eternal...', 'Solitude with the Eternal', 'Solitude with the Eternal...'),
    ('Ancient Obscurity - \n\t\tKutsukaa', 'Ancient Obscurity - Kutsukaa', 'Ancient Obscurity - Kutsukaa'),
    ('Motörhead', 'Motorhead', 'Motorhead'), ('Accu§er', 'Accuser', 'Accuser'), ('D.R.I.', 'D.R.I', 'D.R.I.'),
    ('AC/DC', 'AC-DC', 'AC-DC'), ('Sin.', 'Sin', 'Sin'),
])
def test_clean(text, plain, title):
    assert t.clean(text) == plain
    assert t.clean(text, keep_trailing=True) == title


_quality = {'q': '16-44'}


def _register():
    register_script_function(lambda parser: _quality['q'], name='album_quality')
    register_script_function(lambda parser: parser.context['media'], name='album_media')
    register_script_function(lambda parser, x='': n.first_letter(x), name='first_letter')
    register_script_function(lambda parser, x='': n.media_code(x), name='media_code')
    register_script_function(lambda parser, x='': n.folder_media(x), name='folder_media')
    register_script_function(lambda parser, c='', m='': n.with_media(c, m), name='with_media')
    register_script_function(lambda parser, x='', m='': t.clean(x, keep_trailing='all' if m == 'all' else bool(m)),
                             name='clean')


class LayoutScript(PicardTestCase):
    def setUp(self):
        super().setUp()
        _register()
        import picard.options  # noqa: F401
        from picard import config
        self.set_config_values({o.name: o.default for o in config.Option.registry.values() if o.section == 'setting'})
        self.set_config_values({'windows_compatibility': True, 'ascii_filenames': True, 'replace_dir_separator': '-'})
        self.script = (PLUGIN_DIR / 'metallib_layout.txt').read_text(encoding='utf-8')

    def name(self, quality='16-44', **tags):
        _quality['q'] = quality
        md = Metadata()
        base = {'albumartist': 'Abbath', 'album': 'Dread Reaver', 'title': 'Acid Haze', 'tracknumber': '1',
                'totaldiscs': '1', 'discnumber': '1', 'releasetype': 'album', 'releasestatus': 'official',
                '~dirname': r'C:\in\Abbath - Dread Reaver'}
        base.update(tags)
        if base.get('_ma_band_country_code') and 'releasecountry' not in tags:
            base['releasecountry'] = base['_ma_band_country_code']     # what MetalLib writes before naming
        for k, v in base.items():
            if v is not None:
                md['~' + k[1:] if k.startswith('_') else k] = v       # _x -> hidden ~x (%_x% in scripts)
        return script_to_filename(self.script, md)

    def test_cd_pressing_from_metal_archives(self):
        got = self.name(originalyear='2022', originaldate='2022-03-25', date='2022-03-25', media='CD',
                        catalognumber='SOM 650B', _ma_band_country_code='NO',
                        _releasecomment='Limited edition, Digipak', _releasepackaging='Digipak')
        self.assertEqual(got, 'Abbath (NO)/2022 - Dread Reaver (Digipak) (Lim. Ed.) [SOM 650B CD] [16-44]/'
                              'Abbath - Dread Reaver (Lim. Ed.) - 01 - Acid Haze')

    def test_web_release_has_no_catalog(self):
        got = self.name(originalyear='2023', date='2023-06-09', media='Digital Media', catalognumber='X-1',
                        _ma_band_country_code='SE', releasetype='ep', quality='24-44',
                        albumartist='Alltid Allena', album='Grey Metal', title='Change')
        self.assertEqual(got, 'Alltid Allena (SE)/2023 - Grey Metal (EP) [WEB] [24-44]/'
                              'Alltid Allena - Grey Metal - 01 - Change')

    def test_web_rip_folder_wins_over_cd_tags(self):
        got = self.name(originalyear='2020', media='CD', catalognumber='NB 1', _ma_band_country_code='US',
                        _dirname=r'C:\in\Band-Free_Speech-WEB-2020-GRP')
        self.assertIn('[WEB] [16-44]/', got)

    def test_reissue_needs_a_two_year_gap(self):
        got = self.name(originalyear='1995', date='2008', media='Digital Media', _ma_band_country_code='NO',
                        albumartist='Carpathian Forest', album='Through Chasm, Caves and Titan Woods',
                        releasetype='ep', title='Carpathian Forest')
        self.assertEqual(got, 'Carpathian Forest (NO)/1995 - Through Chasm, Caves and Titan Woods (EP) '
                              '(RE 2008) [WEB] [16-44]/Carpathian Forest - Through Chasm, Caves and Titan Woods '
                              '(RE 2008) - 01 - Carpathian Forest')
        self.assertNotIn('(RE', self.name(originalyear='1994', date='1995', media='CD'))

    def test_remaster_note_makes_rm(self):
        got = self.name(originalyear='1989', date='2012', media='CD', catalognumber='SPI420CD',
                        _releasecomment='remastered')
        self.assertIn('(RM 2012) [SPI420 CD]', got)
        self.assertNotIn('(RE', got)

    def test_japanese_edition_and_multidisc(self):
        got = self.name(originalyear='1988', date='1988', media='CD', _pressingcountry='JP', catalognumber='P33D-20077',
                        _ma_band_country_code='US', albumartist='Anthrax', album='State of Euphoria',
                        title='Be All, End All', totaldiscs='2', discnumber='1')
        self.assertEqual(got, 'Anthrax (US)/1988 - State of Euphoria (Jap. Ed.) [P33D-20077 CD] [16-44]/'
                              'Anthrax - State of Euphoria (Jap. Ed.) - 1-01 - Be All, End All')

    def test_folder_country_is_the_releasecountry_tag_else_xu(self):
        self.assertTrue(self.name(media='CD', _ma_band_country_code='XW').startswith('Abbath (XW)/'))  # International
        self.assertTrue(self.name(media='CD', releasecountry='NO').startswith('Abbath (NO)/'))
        self.assertTrue(self.name(media='CD').startswith('Abbath (XU)/'))                        # unknown

    def test_names_are_ascii_like_the_library(self):
        got = self.name(originalyear='2022', media='Digital Media', albumartist='5 Stikhiy', album='MMXXI A.D.',
                        title='Пробуждение', _ma_band_country_code='RU')
        self.assertEqual(got, '5 Stikhiy (RU)/2022 - MMXXI A.D. [WEB] [16-44]/'
                              '5 Stikhiy - MMXXI A.D. - 01 - Probuzhdenie')

    def test_mixed_quality_and_untitled_track(self):
        got = self.name(quality='Mixed', originalyear='2001', media='CD', title='', _filename='01-copse-_')
        self.assertIn('[CD] [Mixed]/', got)
        self.assertTrue(got.endswith(' - 01 - 01-copse-_'))

    def test_artist_dot_kept_in_folder_and_files_even_with_unknown_country(self):
        got = self.name(originalyear='2022', media='CD', albumartist='Deadvoid Inc.', album='Chapters',
                        title='Grounding the Unreal')
        self.assertTrue(got.startswith('Deadvoid Inc. (XU)/2022 - Chapters [CD] [16-44]/Deadvoid Inc. - Chapters - 01'), got)

    def test_artist_keeps_its_dot_in_file_names_and_the_folder(self):
        got = self.name(originalyear='1994', media='CD', catalognumber='4509-98058-2', albumartist='A.N.I.M.A.L.',
                        album='Fin de un mundo enfermo', title='Solo por ser indios', _ma_band_country_code='AR')
        self.assertEqual(got, 'A.N.I.M.A.L. (AR)/1994 - Fin de un mundo enfermo [4509-98058-2 CD] [16-44]/'
                              'A.N.I.M.A.L. - Fin de un mundo enfermo - 01 - Solo por ser indios')

    def test_showcat_puts_the_catalog_bracket_into_file_names(self):
        got = self.name(originalyear='1999', media='CD', catalognumber='063 683-2', showcat='1',
                        albumartist='Apocalyptica', album='Reflections', title='Prologue', _ma_band_country_code='FI')
        self.assertEqual(got, 'Apocalyptica (FI)/1999 - Reflections [063 683-2 CD] [16-44]/'
                              'Apocalyptica - Reflections [063 683-2 CD] - 01 - Prologue')
        plain = self.name(originalyear='1999', media='CD', catalognumber='063 683-2',
                          albumartist='Apocalyptica', album='Reflections', title='Prologue', _ma_band_country_code='FI')
        self.assertTrue(plain.endswith('/Apocalyptica - Reflections - 01 - Prologue'))


class CatnumTag(PicardTestCase):
    @pytest.mark.skipif(sys.platform != 'win32', reason='checks Windows paths')  # audit L9/L7
    def test_catnum_is_the_folder_bracket(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('picard.plugins.metallib_naming_test', PLUGIN_DIR / '__init__.py',
                                                      submodule_search_locations=[str(PLUGIN_DIR)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)

        class F:
            def __init__(self, folder, **tags):
                self.filename = folder + r'\01.flac'
                self.parent_item = None
                self.metadata = Metadata(**tags)
        self.assertEqual(mod.catnum_for(F(r'C:\in\A.N.I.M.A.L.-Fin-ES-CD-FLAC-1994-DeVOiD', media='CD',
                                          catalognumber='4509-98058-2')), '4509-98058-2 CD')
        self.assertEqual(mod.catnum_for(F(r'C:\in\Band - Album', media='CD')), 'CD')           # no catalog
        self.assertEqual(mod.catnum_for(F(r'C:\in\Band-Album-WEB-2023-GRP', media='CD', catalognumber='X1')), 'WEB')
        self.assertEqual(mod.catnum_for(F(r'C:\in\Band - Album')), '')                         # nothing known

    @pytest.mark.skipif(sys.platform != 'win32', reason='checks Windows paths')
    def test_catnum_in_new_value_and_a_typed_one_stays(self):
        # CATNUM is shown before saving (user) and one typed by hand is never replaced
        import importlib.util
        spec = importlib.util.spec_from_file_location('picard.plugins.metallib_naming_test2', PLUGIN_DIR / '__init__.py',
                                                      submodule_search_locations=[str(PLUGIN_DIR)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)

        class F:
            def __init__(self, **tags):
                self.filename = r'C:\in\A.O.K.-Im_Geiste_Schlicht-DE-CD-FLAC-2011-DEMONSKULL\01.flac'
                self.parent_item = None
                self.metadata = Metadata(**tags)
        f = F(media='CD', catalognumber='burnout016')
        self.assertTrue(mod.update_catnum(f))
        self.assertEqual(f.metadata['catnum'], 'burnout016 CD')
        mine = F(media='CD', catalognumber='burnout016', catnum='MY OWN')
        mine.value_sources = {'catnum': 'user'}
        self.assertFalse(mod.update_catnum(mine))
        self.assertEqual(mine.metadata['catnum'], 'MY OWN')



class FolderEndingInDots(LayoutScript):
    def test_album_ending_in_dots_with_nothing_after(self):
        # Audit part 2 L4: "Into the Abyss..." with no bracket or quality after it became ".._"
        got = self.name(quality='', album='Into the Abyss...', originalyear='2001', media='',
                        releasecountry='NO', _dirname=r'C:\in\x')
        self.assertEqual(got.split('/')[1], '2001 - Into the Abyss')
        self.assertIn('Into the Abyss... - 01', got.split('/')[2])          # the file keeps them

    def test_dots_inside_stay(self):
        got = self.name(album='Into the Abyss...', originalyear='2001', media='CD', releasecountry='NO')
        self.assertEqual(got.split('/')[1], '2001 - Into the Abyss... [CD] [16-44]')

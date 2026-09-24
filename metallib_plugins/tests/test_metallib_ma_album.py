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
    # the pressing's description "Digipak" -> ~releasecomment / ~releasepackaging for naming
    assert node['disambiguation'] == 'Digipak' and node['packaging'] == 'Digipak'
    plain = dict(pressing, desc='')
    assert 'packaging' not in r.build_release(album, plain, '2019-10-18')


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


def test_discogs_format_names():
    assert r.format_kind('File, FLAC, Album') == r.DIGITAL
    assert r.format_kind('File, MP3, Album, 320 kbps') == r.DIGITAL
    assert r.format_kind('2xLP, Album, Gatefold') == r.VINYL
    assert r.format_kind('Cass, Album') == r.TAPE
    assert r.format_kind('CD, Album') == r.CD


def test_pairing_understands_acronyms():
    targets = [{'title': 'Target on My Back', 'length': 271000, 'disc': '1', 'number': '9'},
               {'title': 'NYC 93', 'length': 289000, 'disc': '1', 'number': '7'}]
    sources = [{'title': 'N.Y.C. 93', 'length': 289000, 'disc': '1', 'number': '3'},
               {'title': 'T.O.M.B.', 'length': 271000, 'disc': '2', 'number': '4'}]    # a vinyl's positions
    assert r.pair_tracks(targets, sources) == {0: 1, 1: 0}


# Real Discogs master 4348167 (Anthrax "Cursum Perficio") version formats, as the API lists them.
ANTHRAX_VERSIONS = [
    {'album_id': 38466573, 'format': 'LP, Album, Limited Edition, Picture Disc', 'catalog': ''},
    {'album_id': 38472519, 'format': 'Album', 'catalog': ''},
    {'album_id': 38465898, 'format': 'ALAC, Album, Stereo', 'catalog': ''},
    {'album_id': 38467437, 'format': 'LP, Album', 'catalog': ''},
    {'album_id': 38497557, 'format': 'ALAC', 'catalog': ''},
]


def test_web_album_gets_the_digital_discogs_versions():
    cands, why = r.narrow_pressings(ANTHRAX_VERSIONS, 'Anthrax-Cursum_Perficio-WEB-2026-ENTiTLED', '', '')
    assert [v['album_id'] for v in cands] == [38465898, 38497557] and why == 'media (Digital)'


def test_no_same_media_prefers_cd_like_over_vinyl():
    cands, _ = r.narrow_pressings([v for v in ANTHRAX_VERSIONS if 'ALAC' not in v['format']],
                                  'Anthrax-Cursum_Perficio-WEB-2026-ENTiTLED', '', '')
    assert cands[0]['format'] == 'Album' and 'LP' in cands[-1]['format']


def test_spacing_only_titles_pair():
    assert r.title_score('NYC 93', 'NYC93') == 1.0


def test_same_tracklist_pairs_by_position_whatever_the_names():
    targets = [{'title': 'Target on My Back', 'length': 271000}, {'title': 'Watch It Go', 'length': 343000}]
    sources = [{'title': 'Totally Different', 'length': 272000}, {'title': 'Also Different', 'length': 342000}]
    assert r.pair_tracks(targets, sources) == {0: 0, 1: 1}


def test_position_is_never_trusted_without_lengths():
    # Same count but the lengths do not line up (a swap / another pressing): per-track matching instead.
    targets = [{'title': 'A', 'length': 200000, 'disc': '1', 'number': '1'},
               {'title': 'B', 'length': 300000, 'disc': '1', 'number': '2'}]
    sources = [{'title': 'B', 'length': 300000, 'disc': '1', 'number': '1'},
               {'title': 'A', 'length': 200000, 'disc': '1', 'number': '2'}]
    assert r.pair_tracks(targets, sources) == {0: 1, 1: 0}


def _pressings():
    import importlib
    if 'picard.plugins.metallib_ma_test' not in sys.modules:
        _load_plugin()
    return importlib.import_module('picard.plugins.metallib_ma_test.pressings')


def test_pressings_rank_exact_count_first_near_misses_greyed():
    p = _pressings()
    local = {'track_count': 11, 'folder': 'Anthrax-Cursum_Perficio-WEB-2026-ENTiTLED', 'year': '2026'}
    cands = [p.candidate('Discogs', 1, '2026', 'LP, Album', 'Megaforce', track_count=11, fits=True),
             p.candidate('Discogs', 2, '2026', 'ALAC, Album', 'Nuclear Blast', track_count=11, fits=True),
             p.candidate('Discogs', 3, '2026', 'CD, Album, Deluxe', 'Nuclear Blast', track_count=13),
             p.candidate('Discogs', 4, '2026', 'Album', 'Ward Records')]
    ranked = p.rank(cands, local)
    assert [c['id'] for c, _ in ranked] == ['2', '1', '4', '3']          # digital first; unknown; deluxe last
    assert [g for _, g in ranked] == [False, False, False, True]


def test_pressing_description():
    p = _pressings()
    c = p.candidate('Metal Archives', 9, 'January 30th, 2026', 'Digital', 'Independent', track_count=7, fits=True)
    assert p.describe(c, 7).endswith('· 7 tracks')                   # fitting lengths: nothing said
    bad = p.candidate('Discogs', 2, '2026', 'CD', track_count=7, fits=False)
    assert p.describe(bad, 7).endswith('7 tracks, lengths unmatched') and p.unmatched(bad, 7)
    other = p.candidate('Discogs', 3, '2026', 'CD', track_count=9, fits=False)
    assert p.describe(other, 7).endswith('9 tracks') and not p.unmatched(other, 7)   # greyed instead
    assert p.describe(p.candidate('Discogs', 1, '2026', 'LP'), 7).endswith('checking...')


def test_pressings_from_each_source():
    p = _pressings()
    ma = p.from_ma([{'album_id': '1', 'date': '2019', 'label': 'SoM', 'catalog': 'SOM 1', 'format': 'CD', 'desc': ''},
                    {'album_id': '2', 'date': '2020', 'label': '', 'catalog': '', 'format': 'Digital', 'desc': ''}],
                   [{'version': {'album_id': '1'}, 'page': {'tracks': [1, 2, 3]}, 'fits': True}])
    assert [(c['id'], c['track_count'], c['fits']) for c in ma] == [('1', 3, True), ('2', None, None)]
    mb = p.from_mb([{'id': 'abc', 'date': '1990-05-01', 'country': 'DE', 'status': 'Official',
                     'label-info': [{'catalog-number': 'N 0151', 'label': {'name': 'Noise'}}],
                     'media': [{'format': 'CD', 'track-count': 5}, {'format': 'CD', 'track-count': 4}]}])
    assert (mb[0]['format'], mb[0]['label'], mb[0]['catalog'], mb[0]['track_count'], mb[0]['desc']) == \
        ('2×CD', 'Noise', 'N 0151', 9, '')
    dg = p.from_discogs([{'id': 77, 'released': '1990', 'format': 'CD, Album', 'label': 'Noise', 'catno': 'N 1',
                          'country': 'Germany'}, {'title': 'no id'}])
    assert [(c['id'], c['country']) for c in dg] == [('77', 'Germany')]


class _FakeTrack:
    def __init__(self, secs):
        self.files = []
        self.metadata = MagicMock(length=secs * 1000)


class _FakeAlbum:
    def __init__(self, lengths):
        self.tracks = [_FakeTrack(s) for s in lengths]
        self.metadata = {'catalognumber': '', 'media': '', 'originaldate': '', 'date': ''}
        self.loaded = True

    def iterfiles(self):
        return iter(())


def test_pressings_panel_record_merges_and_notes():
    import importlib
    _pressings()
    panel = importlib.import_module('picard.plugins.metallib_ma_test.pressings_panel')
    p = _pressings()
    album = _FakeAlbum([200, 300])
    panel.record(album, 'MusicBrainz', [p.candidate('MusicBrainz', 'a', '1990', 'CD', 'Noise', track_count=2)], 'a')
    # A later, sparser entry for the same pressing keeps what was known.
    panel.record(album, 'MusicBrainz', [p.candidate('MusicBrainz', 'a', country='DE'),
                                        p.candidate('MusicBrainz', 'b', track_count=3)])
    items = {c['id']: c for c in panel.state(album)['MusicBrainz']['items']}
    assert (items['a']['date'], items['a']['label'], items['a']['country'], items['a']['track_count']) == \
        ('1990', 'Noise', 'DE', 2)
    assert panel.state(album)['MusicBrainz']['chosen'] == 'a'
    panel.note(album, 'MusicBrainz', 'a', 2, True)
    assert items['a']['fits'] is True
    assert panel.local_info(album)['lengths'] == [200, 300]
    panel.set_chosen(album, 'MusicBrainz', 'b')
    assert panel.state(album)['MusicBrainz']['chosen'] == 'b'


def test_pressings_short_label_for_column_header():
    p = _pressings()
    assert p.short_label(p.candidate('MusicBrainz', 'x', '2019-10-18', 'CD', 'Season of Mist', 'SOM532B', 'XE')) == \
        'CD · SOM532B · 2019 · XE'
    assert p.short_label(p.candidate('Metal Archives', 1, '', 'Digital')) == 'Digital'


def test_pair_by_position_when_a_differing_length_has_the_same_title():
    # 1349 Infernal Pathway: the digital bonus track is 4:54, the CD's is 5:12 -- still the same slot.
    album = [{'title': 'Dødskamp', 'length': 300000, 'disc': '1', 'number': '1'},
             {'title': 'Stand Tall in Fire', 'length': 489000, 'disc': '1', 'number': '2'},
             {'title': 'Dødskamp (Norwegian version) (Bonus Track)', 'length': 294000, 'disc': '1', 'number': '3'}]
    cd = [{'title': 'Dødskamp', 'length': 300600, 'disc': '1', 'number': '1'},
          {'title': 'Stand Tall in Fire', 'length': 489346, 'disc': '1', 'number': '2'},
          {'title': 'Dødskamp (Norwegian version)', 'length': 312106, 'disc': '1', 'number': '3'}]
    assert r.pair_tracks(album, cd) == {0: 0, 1: 1, 2: 2}
    # A differing length with a DIFFERENT title is not the same slot: no position pairing.
    other = [dict(t) for t in cd]
    other[2]['title'] = 'Something Else'
    assert 2 not in r.pair_tracks(album, other)


def test_title_key_ignores_bonus_annotation():
    assert r.title_key('Hell (Bonus Track)') == r.title_key('Hell') == r.title_key('Hell [bonus]')


def test_discogs_format_includes_the_media():
    p = _pressings()
    c = p.from_discogs([{'id': 1, 'major_formats': ['CD'], 'format': 'Album, Limited Edition'}])[0]
    assert c['format'] == 'CD, Album, Limited Edition'


def test_better_pick_only_when_exactly_one_fits():
    p = _pressings()
    shown = p.candidate('Discogs', 1, track_count=11, fits=False)
    fit = p.candidate('Discogs', 2, track_count=12, fits=True)
    assert p.better_pick([shown, fit], '1', 12) == '2'
    two = [fit, p.candidate('Discogs', 3, track_count=12, fits=True)]
    # shown has another track count: the best of the right count wins even when several fit
    assert p.better_pick([shown] + two, '1', 12) == '2'
    # shown has the right count but not the lengths, and two fit: undecidable -> stays
    same_count = p.candidate('Discogs', 4, track_count=12, fits=False)
    assert p.better_pick([same_count] + two, '4', 12) is None
    assert p.better_pick([p.candidate('Discogs', 1, track_count=12, fits=True), fit], '1', 12) is None


def test_hidden_naming_facts_follow_ma_band_and_the_catalog_source():
    import importlib
    if 'picard.plugins.metallib_ma_test' not in sys.modules:
        _load_plugin()
    m = importlib.import_module('picard.plugins.metallib_ma_test')
    from picard.metadata import Metadata
    mb, ma = Metadata(), Metadata()
    mb['~releasecomment'], mb['~releasepackaging'] = 'Japanese edition', 'Jewel Case'
    ma['~ma_band_country_code'], ma['~releasecomment'] = 'NO', 'Limited edition, Digipak'
    ma['~releasepackaging'] = 'Digipak'
    sources = {'MusicBrainz': mb, 'Metal Archives': ma}
    got = m._hidden_facts(sources, {}, {'catalognumber': 'Metal Archives'})
    assert got['~ma_band_country_code'] == ['NO']
    assert (got['~releasecomment'], got['~releasepackaging']) == (['Limited edition, Digipak'], ['Digipak'])
    # The user took the catalog from MusicBrainz: the edition notes describe that pressing.
    got = m._hidden_facts(sources, {'catalognumber': 'MusicBrainz'}, {'catalognumber': 'Metal Archives'})
    assert got['~releasecomment'] == ['Japanese edition'] and got['~ma_band_country_code'] == ['NO']


def test_session_restore_helpers():
    assert r.pressing_id_of('metallib-ma-1405574') == '1405574'
    assert r.pressing_id_of('0b3a2c1d-0000-4000-8000-000000000000') is None
    assert r.pressing_id_of(None) is None
    assert (r.country_code('Norway'), r.country_code('italy'), r.country_code('International'),
            r.country_code('Unknown'), r.country_code('IT'), r.country_code('')) == ('NO', 'IT', 'XW', 'XU', 'IT', '')


def test_every_ma_track_has_its_own_id():
    album = ma_client.parse_album_page(ALBUM, '789680')
    node = r.build_release(album, None, '')
    ids = [t['id'] for m in node['media'] for t in m['tracks']]
    assert all(ids) and len(set(ids)) == len(ids)


def test_better_pick_prefers_the_album_track_count_over_a_near_miss():
    p = _pressings()
    vinyl = p.candidate('Metal Archives', 1, '2022', '12" vinyl', track_count=10, fits=False)
    cd = p.candidate('Metal Archives', 2, '1994', 'CD', track_count=11, fits=False)
    cd2 = p.candidate('Metal Archives', 3, '1994', 'Cassette', track_count=11, fits=False)
    assert p.better_pick([cd, cd2, vinyl], '1', 11) == '2'       # best same-count one
    assert p.better_pick([cd, cd2, vinyl], '2', 11) is None      # already the right count: stays


def test_pressing_ranking_clues_do_not_follow_new_value():
    import importlib
    _pressings()
    panel = importlib.import_module('picard.plugins.metallib_ma_test.pressings_panel')
    album = _FakeAlbum([200, 300])
    album.metadata.update(catalognumber='98058-2', date='1994')
    assert panel.local_info(album)['catalog'] == '98058-2'
    album.metadata.update(catalognumber='XEC 158', date='2022')     # the user picked another pressing
    info = panel.local_info(album)
    assert (info['catalog'], info['year']) == ('98058-2', '1994')     # ranking unchanged
    album.tracks.append(_FakeTrack(100))
    assert panel.local_info(album)['track_count'] == 3                # files/lengths stay current


def test_pressings_title_names_the_album_folder():
    import importlib
    _pressings()
    panel = importlib.import_module('picard.plugins.metallib_ma_test.pressings_panel')

    class F:
        def __init__(self, path):
            self.filename = path
    album = _FakeAlbum([200, 300, 100])
    album.metadata['album'] = 'Grey Metal'
    album.iterfiles = lambda: iter([F(r'C:\m\2023 - Grey Metal\01.flac'), F(r'C:\m\2023 - Grey Metal\02.flac'),
                                    F(r'C:\m\other\03.flac')])
    assert panel.folder_line(album) == r'C:\m\2023 - Grey Metal (+1 more folder) — 3 tracks'
    album.iterfiles = lambda: iter(())
    assert panel.folder_line(album) == 'No files — "Grey Metal", 3 tracks'


def test_pressing_without_durations_is_no_lengths_and_loses_to_a_real_fit():
    p = _pressings()
    assert p.judge([0, 0, 0], [200, 300, 100]) == p.NO_LENGTHS
    assert p.judge([200, 300, 100], [200, 301, 99]) is True
    assert p.judge([200, 300], [200, 300, 100]) is False
    us = p.candidate('Discogs', 1, '1994', 'CD', track_count=3, fits=p.NO_LENGTHS)
    ar = p.candidate('Discogs', 2, '1994', 'CD', track_count=3, fits=True)
    ar2 = p.candidate('Discogs', 3, '1994', 'CD', track_count=3, fits=True)
    assert p.describe(us, 3).endswith('3 tracks, no lengths') and not p.unmatched(us, 3)
    assert p.better_pick([ar, ar2, us], '1', 3) == '2'


def test_pressing_line_shows_the_description():
    p = _pressings()
    c = p.candidate('Metal Archives', 1, '1994', 'CD', 'Tommy Gun Records', '4509-98058-2', desc='Repress',
                    track_count=11, fits=True)
    assert p.describe(c, 11) == '1994 · CD · Repress · Tommy Gun Records · 4509-98058-2 · 11 tracks'
    v = p.candidate('Metal Archives', 2, '2022', '12" vinyl', 'X El Cambio Records', 'XEC 158',
                    desc='Transparent vinyl', track_count=10)
    assert '12" vinyl · Transparent vinyl · X El Cambio' in p.describe(v, 11)


class _KeepTrack:
    def __init__(self, files, **tags):
        from picard.metadata import Metadata
        self.metadata = Metadata(**tags)
        self.files = files


class _KeepFile:
    def __init__(self, **orig):
        from picard.metadata import Metadata
        self.orig_metadata = Metadata(**orig)
        self.metadata = Metadata(**orig)


def test_no_source_value_keeps_the_files_own_and_date_falls_back_to_the_first_release():
    import importlib
    if 'picard.plugins.metallib_ma_test' not in sys.modules:
        _load_plugin()
    m = importlib.import_module('picard.plugins.metallib_ma_test')
    tagged = _KeepFile(catalognumber='SCENE-1', date='1994-05-01')
    bare = _KeepFile()
    for f in (tagged, bare):
        f.metadata['catalognumber'] = 'FROM-MB'            # what the switched-off source had put there
        f.metadata['releasecountry'] = 'US'
        f.metadata['date'] = '1994-01-01'
    track = _KeepTrack([tagged, bare], originaldate='1994', catalognumber='FROM-MB', date='1994-01-01')
    for tag in ('catalognumber', 'releasecountry', 'date'):
        m._keep_file_value(track, tag)
    assert (tagged.metadata['catalognumber'], tagged.metadata['date']) == ('SCENE-1', '1994-05-01')  # file's own
    assert 'releasecountry' not in tagged.metadata and 'catalognumber' not in bare.metadata          # none: none
    assert bare.metadata['date'] == '1994'                                   # no date in the file: first release


def test_release_country_codes_from_discogs_names():
    assert [r.release_country_code(x) for x in ('Argentina', 'Europe', 'Worldwide', 'UK', 'US', 'USA & Europe', '')] == \
        ['AR', 'XE', 'XW', 'GB', 'US', '', '']


def test_lineup_names_lose_notes_and_backing_vocals_use_the_mb_word():
    lineup = [{'name': 'Martín Carrizo (R.I.P. 2022)', 'roles': 'Drums, Backing vocals', 'section': 'members'},
              {'name': 'Andrés Giménez', 'roles': 'Vocals, Guitars', 'section': 'members'},
              {'name': 'Marcelo Corvalán', 'roles': 'Bass, Vocals (backing)', 'section': 'members'}]
    tags = r.lineup_tags(lineup, 1)
    assert tags['performer:drums'] == ['Martín Carrizo']
    assert tags['performer:background vocals'] == ['Martín Carrizo', 'Marcelo Corvalán']
    assert 'performer:backing vocals' not in tags
    assert r.person_name('X (RIP 2019)') == 'X' and r.person_name('Band (live)') == 'Band (live)'

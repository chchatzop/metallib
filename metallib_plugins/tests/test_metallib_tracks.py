# SPDX-License-Identifier: GPL-2.0-or-later
from pathlib import Path
import sys

from picard.similarity import similarity2


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_tracks'))
from placement import (  # noqa: E402
    OK,
    RENUMBERED,
    UNPLACED,
    place,
)


def s(mmss):
    m, sec = mmss.split(':')
    return (int(m) * 60 + int(sec)) * 1000


# 1349 - The Infernal Pathway, disc 1 (real MusicBrainz titles and lengths).
SOURCE = [
    ('Abyssos antithesis', '5:30'), ('Through Eyes of Stone', '3:24'), ('Tunnel of Set VIII', '0:47'),
    ('Enter Cold Void Dreaming', '3:58'), ('Towers upon Towers', '4:50'), ('Tunnel of Set IX', '1:06'),
]
TRACKS = [{'title': t, 'length': s(d), 'number': str(i + 1)} for i, (t, d) in enumerate(SOURCE)]


def files(*specs):
    """spec: (title, length 'm:ss' or None, tracknumber tag or '')."""
    return [{'title': t or '', 'length': s(d) if d else 0, 'tracknumber': n} for t, d, n in specs]


def run(fs, tracks=TRACKS):
    return [(r['track'], r['status']) for r in place(fs, tracks, similarity2)]


def test_clean_album_all_ok():
    fs = files(*[(t, d, str(i + 1)) for i, (t, d) in enumerate(SOURCE)])
    assert run(fs) == [(i, OK) for i in range(6)]


def test_shuffled_files_land_on_their_own_tracks():
    order = [4, 0, 5, 2, 1, 3]
    fs = files(*[(SOURCE[i][0], SOURCE[i][1], '') for i in order])
    assert [t for t, _ in run(fs)] == order


def test_similar_titles_split_by_duration():
    # "Tunnel of Set VIII" vs "IX": titles are close, lengths (0:47 / 1:06) are not.
    fs = files(('Tunnel of Set VIII', '0:47', ''), ('Tunnel of Set IX', '1:06', ''))
    assert run(fs) == [(2, OK), (5, OK)]


def test_no_titles_no_numbers_placed_by_duration():
    fs = files(*[('', d, '') for _, d in reversed(SOURCE)])
    assert [t for t, _ in run(fs)] == [5, 4, 3, 2, 1, 0]


def test_junk_titles_fall_back_to_duration():
    fs = files(('Track 01', '3:24', ''), ('Track 02', '5:30', ''))
    assert run(fs) == [(1, OK), (0, OK)]


def test_swapped_titles_are_refused_not_trusted():
    # The file-swap bug: title tags were written onto the wrong audio. The title points to one
    # track, the audio length to another -> flag both, move neither onto a wrong track.
    fs = files(('Abyssos antithesis', '3:24', '1'), ('Through Eyes of Stone', '5:30', '2'))
    res = place(fs, TRACKS, similarity2)
    assert [r['status'] for r in res] == [UNPLACED, UNPLACED]
    assert 'but the audio is 3:24 vs 5:30' in res[0]['reason']


def test_offset_numbering_is_renumbered():
    # A Faith Unkind case: the ripper counted the cover as 01, tracks are tagged 02..07.
    fs = files(*[(t, d, str(i + 2)) for i, (t, d) in enumerate(SOURCE)])
    res = place(fs, TRACKS, similarity2)
    assert [(r['track'], r['status']) for r in res] == [(i, RENUMBERED) for i in range(6)]
    assert res[0]['reason'] == 'track number tag said 2, title+duration says 1'


def test_zero_padded_and_slash_tags_are_not_renumbered():
    fs = files(('Abyssos antithesis', '5:30', '01'), ('Through Eyes of Stone', '3:24', '2/6'))
    assert run(fs) == [(0, OK), (1, OK)]


def test_ambiguous_duration_without_titles_is_refused():
    tracks = [{'title': 'Intro', 'length': s('1:30'), 'number': '1'},
              {'title': 'Interlude', 'length': s('1:32'), 'number': '2'},
              {'title': 'Epic', 'length': s('9:00'), 'number': '3'}]
    fs = files(('', '1:31', ''), ('', '9:01', ''))
    res = place(fs, tracks, similarity2)
    assert [(r['track'], r['status']) for r in res] == [(None, UNPLACED), (2, OK)]
    assert 'several tracks have a similar length (1, 2)' in res[0]['reason']


def test_part_one_and_two():
    tracks = [{'title': 'The Serpent, Part I', 'length': s('6:10'), 'number': '1'},
              {'title': 'The Serpent, Part II', 'length': s('7:45'), 'number': '2'}]
    fs = files(('The Serpent, Part II', '7:44', ''), ('The Serpent, Part I', '6:11', ''))
    assert run(fs, tracks) == [(1, OK), (0, OK)]


def test_same_title_same_length_is_refused():
    tracks = [{'title': 'Reprise', 'length': s('2:00'), 'number': '4'},
              {'title': 'Reprise', 'length': s('2:02'), 'number': '9'}]
    fs = files(('Reprise', '2:01', ''))
    assert run(fs, tracks) == [(None, UNPLACED)]


def test_two_copies_of_one_track_are_both_refused():
    fs = files(('Towers upon Towers', '4:50', '5'), ('Towers upon Towers', '4:50', '5'))
    res = place(fs, TRACKS, similarity2)
    assert [r['status'] for r in res] == [UNPLACED, UNPLACED]
    assert res[0]['reason'] == '2 files match track 5 "Towers upon Towers"'


def test_bonus_suffix_and_diacritics():
    tracks = [{'title': 'Dødskamp', 'length': s('5:01'), 'number': '1'},
              {'title': 'Stand Tall in Fire (Bonus Track)', 'length': s('8:09'), 'number': '2'}]
    fs = files(('Stand Tall in Fire', '8:10', ''), ('Dodskamp', '5:00', ''))
    assert run(fs, tracks) == [(1, OK), (0, OK)]


def test_source_without_lengths_uses_titles_only():
    tracks = [dict(t, length=0) for t in TRACKS]
    fs = files(('Towers upon Towers', '4:50', ''), ('', '3:24', ''))
    res = place(fs, tracks, similarity2)
    assert [(r['track'], r['status']) for r in res] == [(4, OK), (None, UNPLACED)]
    assert res[1]['reason'] == 'no title match and the source has no track lengths'


def test_bonus_file_not_on_this_release_is_refused():
    fs = files(('A Song Not On This Pressing', '4:12', '7'))
    assert run(fs) == [(None, UNPLACED)]


def test_radio_edit_goes_to_its_own_track():
    # Hellripper "Ex Infernus" case: "Incarnate" and "Incarnate (Radio Edit)" on one release.
    tracks = [{'title': 'Incarnate', 'length': s('4:05'), 'number': '2'},
              {'title': 'Incarnate (Radio Edit)', 'length': s('3:58'), 'number': '8'}]
    fs = files(('Incarnate (Radio Edit)', '4:01', ''), ('Incarnate', '4:02', ''))
    assert run(fs, tracks) == [(1, OK), (0, OK)]


def test_elimination_resolves_duration_ties_without_guessing():
    # No titles: file A (1:31) fits tracks 1 and 2; file B (1:33) only fits track 2 once track
    # 1's owner is known... here track 1 is taken by a titled file, which frees the decision.
    tracks = [{'title': 'Intro', 'length': s('1:30'), 'number': '1'},
              {'title': 'Interlude', 'length': s('1:34'), 'number': '2'}]
    fs = files(('Intro', '1:30', ''), ('', '1:33', ''))
    assert run(fs, tracks) == [(0, OK), (1, OK)]


def test_second_copy_never_slides_onto_another_track():
    tracks = [{'title': 'Towers upon Towers', 'length': s('4:50'), 'number': '1'},
              {'title': 'Deeper Still', 'length': s('4:52'), 'number': '2'}]
    fs = files(('Towers upon Towers', '4:50', ''), ('Towers upon Towers', '4:51', ''))
    res = place(fs, tracks, similarity2)
    assert [r['status'] for r in res] == [UNPLACED, UNPLACED]


def test_copy_of_a_placed_track_stays_unplaced():
    tracks = [{'title': 'Towers upon Towers', 'length': s('4:50'), 'number': '1'},
              {'title': 'Deeper Still', 'length': s('4:52'), 'number': '2'}]
    fs = files(('Towers upon Towers', '4:50', ''), ('Deeper Still', '4:52', ''),
               ('Towers upon Towers', '4:51', ''))
    res = place(fs, tracks, similarity2)
    assert [r['status'] for r in res] == [UNPLACED, OK, UNPLACED]


# --- Integration: resolve() moving real Picard File objects between real Tracks -----------------

import importlib.util  # noqa: E402
import types  # noqa: E402

from test.picardtestcase import PicardTestCase  # noqa: E402

from picard.album import Album  # noqa: E402
from picard.file import File  # noqa: E402
from picard.track import Track  # noqa: E402


def _load_tracks_plugin():
    spec = importlib.util.spec_from_file_location(
        'picard.plugins.metallib_tracks_test',
        Path(__file__).resolve().parent.parent / 'metallib_tracks' / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestResolveInPicard(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.plugin = _load_tracks_plugin()
        self.plugin._originals['tracknum_and_title_from_filename'] =             __import__('picard.util', fromlist=['x']).tracknum_and_title_from_filename
        from unittest.mock import MagicMock
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.plugin._api = types.SimpleNamespace(
            tagger=self.tagger, logger=types.SimpleNamespace(
                debug=lambda *a, **k: None, info=lambda *a, **k: None, exception=self._raise))
        import picard.options  # noqa: F401 -- registers every option with its default
        from picard.config import Option
        self.set_config_values(setting={name: opt.default for (section, name), opt in Option.registry.items()
                                        if section == 'setting'})
        self.album = Album('00000000-0000-0000-0000-000000000003')
        for i, (title, length) in enumerate(SOURCE):
            t = Track('00000000-0000-0000-0000-0000000003%02d' % i, self.album)
            t.metadata['title'] = title
            t.metadata['tracknumber'] = str(i + 1)
            t.metadata.length = s(length)
            self.album.tracks.append(t)

    @staticmethod
    def _raise(*a, **k):
        raise   # re-raise inside the plugin's except block, so tests see real errors

    def _file(self, name, title, length, tracknumber=''):
        f = File(name)
        f.state = File.State.NORMAL
        for md in (f.orig_metadata, f.metadata):
            if title:
                md['title'] = title
            if tracknumber:
                md['tracknumber'] = tracknumber
            md.length = s(length)
        self.album.unmatched_files.add_file(f)
        return f

    def test_resolve_places_renumbers_and_refuses(self):
        a = self._file('a.flac', 'Towers upon Towers', '4:50', '3')          # renumbered 3 -> 5
        b = self._file('b.flac', '', '0:47')                                  # duration only -> 3
        c = self._file('c.flac', 'Abyssos antithesis', '3:24', '1')           # swapped audio
        self.plugin.resolve(self.album, [a, b, c])
        self.assertIs(a.parent_item, self.album.tracks[4])
        self.assertIs(b.parent_item, self.album.tracks[2])
        # c's title tag says track 1 but its audio is 3:24 = track 2, the only free track with that length:
        # placed there by the "last free track" rule and flagged (user rule 2026-09-23).
        self.assertIs(c.parent_item, self.album.tracks[1])
        self.assertEqual(a.metadata['~placement'], 'renumbered')
        self.assertEqual(a.metadata['tracknumber'], '5')                      # takes the track's number
        self.assertEqual(b.metadata['~placement'], '')
        self.assertEqual(c.metadata['~placement'], 'assumed')
        self.assertIn('does not match', c.metadata['~placement_reason'])

    def test_track_already_holding_a_file_is_not_doubled(self):
        keep = self._file('keep.flac', 'Towers upon Towers', '4:50')
        keep.move(self.album.tracks[4])                                       # placed by hand earlier
        dup = self._file('dup.flac', 'Towers upon Towers', '4:50')
        self.plugin.resolve(self.album, [dup])
        self.assertIs(keep.parent_item, self.album.tracks[4])
        self.assertIs(dup.parent_item, self.album.unmatched_files)
        self.assertEqual(dup.metadata['~placement'], 'unplaced')

    def test_filename_never_gives_a_track_number(self):
        from picard.metadata import Metadata
        md = Metadata()
        self.plugin._guess_title_only(File('07 - Some Song.flac'), md)
        self.assertEqual(md['title'], 'Some Song')
        self.assertEqual(md['tracknumber'], '')

    def test_title_from_filename(self):
        g = self.plugin.title_from_filename
        self.assertEqual(g('1349 - The Infernal Pathway - 01 - Abyssos antithesis.flac'), 'Abyssos antithesis')
        self.assertEqual(g('07. Some Song.mp3'), 'Some Song')
        self.assertEqual(g('1-03 Tunnel of Set VIII.flac'), 'Tunnel of Set VIII')
        self.assertEqual(g('Band - Album - 12 - 1000 Years of War.mp3'), '1000 Years of War')
        self.assertEqual(g('Untitled.flac'), 'Untitled')


# --- Wrong-release guard -------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_tracks'))
from release_check import (  # noqa: E402
    OK as R_OK,
    SUSPECT,
    WRONG,
    artist_key,
    check_release,
    same_artist,
    track_count_compatible,
)


def test_artist_key_folding():
    assert artist_key('Motörhead') == artist_key('MOTORHEAD') == 'motorhead'
    assert artist_key('AC/DC') == artist_key('AC-DC')
    assert artist_key('The Cure') == 'cure'
    assert artist_key('Dødheimsgard') == 'dodheimsgard'
    assert artist_key('Death') != artist_key('Deathspell Omega')


def test_same_artist():
    assert same_artist('Blut aus Nord', 'Blut aus Nord / AEvangelist', similarity2)   # split
    assert same_artist('Mgła', 'Mgla', similarity2)
    assert not same_artist('Aghar', 'Mortlach', similarity2)
    assert not same_artist('Alltid Allena', 'Tapani Rinne', similarity2)


def test_track_count():
    assert track_count_compatible(11, 13)          # bonus-track edition
    assert not track_count_compatible(4, 12)       # EP vs album
    assert track_count_compatible(0, 12)           # unknown


def test_real_wrong_lookups_from_the_screenshot():
    # Aghar "Cellar of the Castle" (7 files) -> Mortlach "Relics of the Castle" (9 tracks)
    v, why = check_release(7, 0, 9, ['Aghar'] * 7, 'Mortlach', similarity2)
    assert v == WRONG and '0 of 7 files fit' in why and '"Aghar"' in why
    # Alltid Allena "Grey Metal" (7) -> Tapani Rinne "Grey" (7)
    assert check_release(7, 0, 7, ['Alltid Allena'] * 7, 'Tapani Rinne', similarity2)[0] == WRONG


def test_right_release_under_another_spelling_is_kept():
    # MusicBrainz credits the band in another script, but the files fit: keep it.
    assert check_release(8, 8, 8, ['Nargaroth'] * 8, 'Наргарот', similarity2) == (R_OK, '')


def test_untitled_files_on_the_right_release_are_not_thrown_out():
    # Nothing placed (no titles, lengths too close) but artist and count agree: not wrong.
    assert check_release(6, 0, 6, ['1349'] * 6, '1349', similarity2) == (R_OK, '')


def test_wrong_edition_by_track_count():
    # A 3-track single's files looked up onto the 12-track album of the same band.
    assert check_release(3, 0, 12, ['Darkthrone'] * 3, 'Darkthrone', similarity2)[0] == WRONG


def test_half_fitting_is_suspect():
    assert check_release(10, 4, 10, ['Aghar'] * 10, 'Mortlach', similarity2)[0] == SUSPECT


def test_various_artists_release_skips_the_artist_check():
    assert check_release(12, 12, 12, ['A', 'B', 'C'] * 4, 'Various Artists', similarity2) == (R_OK, '')


class TestWrongReleaseGuardInPicard(TestResolveInPicard):
    def setUp(self):
        super().setUp()
        from unittest.mock import MagicMock, patch
        from picard.cluster import UnclusteredFiles
        self.album.metadata['album'] = 'Relics of the Castle'
        self.album.metadata['albumartist'] = 'Mortlach'
        self.album.loaded = True
        self.tagger.albums = {self.album.id: self.album}
        self.tagger.unclustered_files = UnclusteredFiles()
        self.tagger.remove_album = MagicMock()
        self.tagger.cluster = MagicMock()
        self.tagger.window = MagicMock()
        patcher = patch.object(self.plugin.QtCore.QTimer, 'singleShot', side_effect=lambda ms, fn: fn())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _aghar(self, name, title, length):
        f = self._file(name, title, length)
        f.orig_metadata['artist'] = 'Aghar'
        return f

    def test_wrong_release_is_taken_apart(self):
        fs = [self._aghar('k.flac', 'King Winter', '4:54'), self._aghar('c.flac', 'Cellar of the Castle', '4:28')]
        self.plugin._match_files(self.album, fs)
        self.tagger.remove_album.assert_called_once_with(self.album)
        self.tagger.cluster.assert_called_once()
        for f in fs:
            self.assertIs(f.parent_item, self.tagger.unclustered_files)
            self.assertEqual(f.metadata['~placement'], 'unplaced')
            self.assertIn('wrong release "Relics of the Castle" by Mortlach', f.metadata['~placement_reason'])

    def test_guard_runs_once_not_on_manual_drops(self):
        self.plugin._match_files(self.album, [self._aghar('k.flac', 'King Winter', '4:54')])
        self.tagger.remove_album.reset_mock()
        self.plugin._match_files(self.album, [self._aghar('x.flac', 'Other', '1:00')])
        self.tagger.remove_album.assert_not_called()

    def test_right_release_is_kept(self):
        self.album.metadata['albumartist'] = '1349'
        f = self._file('a.flac', 'Towers upon Towers', '4:50')
        f.orig_metadata['artist'] = '1349'
        self.plugin._match_files(self.album, [f])
        self.tagger.remove_album.assert_not_called()
        self.assertIs(f.parent_item, self.album.tracks[4])


def test_real_title_matching_nothing_is_not_placed_by_duration():
    # Aghar "King Winter" (4:54) on Mortlach's album must not land on a 4:50 track.
    res = place(files(('King Winter', '4:54', '')), TRACKS, similarity2)
    assert res[0]['status'] == UNPLACED
    assert res[0]['reason'] == 'title "King Winter" matches no track of this release'


def test_junk_titles_count_as_no_title():
    for junk in ('Track 01', 'Untitled', 'Audio Track 3', '05', 'track-7', 'Unknown'):
        assert run(files((junk, '3:24', ''))) == [(1, OK)], junk


def test_length_coincidence_with_a_different_title_is_refused():
    tracks = [{'title': 'We Are the Only Ones', 'length': s('3:43'), 'number': '1'},
              {'title': 'Executioner', 'length': s('6:10'), 'number': '2'}]
    res = place(files(('Executed on Site', '3:46', '')), tracks, similarity2)
    assert res[0]['status'] == UNPLACED


# --- AcoustID fingerprints ------------------------------------------------------------------------

def _fp(tracks, recs):
    return [dict(t, recording_ids={r}) for t, r in zip(tracks, recs)]


def test_fingerprint_places_untitled_file_duration_could_not():
    tracks = _fp([{'title': 'Intro', 'length': s('1:30'), 'number': '1'},
                  {'title': 'Interlude', 'length': s('1:32'), 'number': '2'}], ['rec-1', 'rec-2'])
    fs = [{'title': '', 'length': s('1:31'), 'tracknumber': '', 'recording_ids': {'rec-2'}}]
    res = place(fs, tracks, similarity2)
    assert (res[0]['track'], res[0]['reason']) == (1, 'fingerprint')


def test_fingerprint_beats_a_swapped_title_tag():
    # Old retag bug: this file's TITLE tag says "Abyssos antithesis" but its audio is track 2.
    tracks = _fp(TRACKS[:2], ['rec-1', 'rec-2'])
    fs = [{'title': 'Abyssos antithesis', 'length': s('3:24'), 'tracknumber': '1', 'recording_ids': {'rec-2'}}]
    res = place(fs, tracks, similarity2)
    assert res[0]['track'] == 1 and res[0]['status'] == RENUMBERED


def test_fingerprint_with_contradicting_length_is_refused():
    tracks = _fp(TRACKS[:1], ['rec-1'])
    fs = [{'title': '', 'length': s('9:00'), 'tracknumber': '', 'recording_ids': {'rec-1'}}]
    assert place(fs, tracks, similarity2)[0]['status'] == UNPLACED


def test_fingerprint_of_a_taken_track_is_refused():
    tracks = _fp(TRACKS[:2], ['rec-1', 'rec-2'])
    fs = [{'title': 'Abyssos antithesis', 'length': s('5:30'), 'tracknumber': ''},
          {'title': '', 'length': s('5:30'), 'tracknumber': '', 'recording_ids': {'rec-1'}}]
    res = place(fs, tracks, similarity2)
    assert [r['status'] for r in res] == [UNPLACED, UNPLACED]      # both claim track 1: nobody gets it


def test_unknown_fingerprint_falls_back_to_title_and_duration():
    tracks = _fp(TRACKS[:2], ['rec-1', 'rec-2'])
    fs = [{'title': 'Through Eyes of Stone', 'length': s('3:24'), 'tracknumber': '', 'recording_ids': {'other'}}]
    assert run(fs, tracks) == [(1, OK)]


class TestFingerprintInPicard(TestResolveInPicard):
    def test_resolve_uses_fingerprint_recordings(self):
        for i, t in enumerate(self.album.tracks):
            t.metadata['musicbrainz_recordingid'] = 'rec-%d' % i
        f = self._file('x.flac', '', '0:47')          # untitled, fits 0:47 (track 3) -- but so could others
        setattr(f, self.plugin._FP_ATTR, {'rec-2'})
        self.plugin.resolve(self.album, [f])
        self.assertIs(f.parent_item, self.album.tracks[2])

    def test_mb_column_ids_are_used_for_ma_albums(self):
        from picard.metadata import Metadata
        t = self.album.tracks[4]
        mb = Metadata()
        mb['musicbrainz_recordingid'] = 'rec-from-mb-column'
        t.source_metadata = {'MusicBrainz': mb}
        self.assertEqual(self.plugin._recording_ids_of_track(t), {'rec-from-mb-column'})


def test_initialism_title_is_placed():
    # Anthrax "Cursum Perficio": the file is tagged "T.O.M.B.", the source says "Target on My Back";
    # track 04 (4:36) is within 5 s, so duration alone could not decide.
    tracks = [{'title': "Everybody's Got a Plan", 'length': s('4:36'), 'number': '4'},
              {'title': 'Target on My Back', 'length': s('4:31'), 'number': '9'}]
    res = place(files(('T.O.M.B.', '4:31', '')), tracks, similarity2)
    assert (res[0]['track'], res[0]['status']) == (1, OK)


def test_initialism_needs_matching_words_and_length():
    tracks = [{'title': 'Target on My Back', 'length': s('4:31'), 'number': '9'}]
    assert place(files(('T.O.M.B.', '7:00', '')), tracks, similarity2)[0]['status'] == UNPLACED   # length contradicts
    tracks2 = [{'title': 'Tomb of the Mutilated', 'length': s('4:31'), 'number': '1'}]
    assert run(files(('T.O.M.B.', '4:31', '')), tracks2) == [(None, UNPLACED)]   # "totm" is not "tomb"


def test_is_initialism_of():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_tracks'))
    import placement as p
    assert p.is_initialism_of('T.O.M.B.', 'Target on My Back')
    assert p.is_initialism_of('TOMB', 'Target on My Back')
    assert not p.is_initialism_of('T.O.M.B.', 'Tomb of the Mutilated')
    assert not p.is_initialism_of('Tomb', 'Target on My Back')          # an ordinary word, not an acronym


# --- last free track (user rule: 1 empty spot + 1 leftover file with the same duration) ------------

from placement import ASSUMED  # noqa: E402


def test_last_free_track_is_assumed():
    # Anthrax: 10 files placed by title, "Target on My Back" empty, a file titled "Something Else" 4:31.
    tracks = [{'title': 'Song %d' % i, 'length': s('3:%02d' % (10 + i * 4)), 'number': str(i + 1)} for i in range(10)]
    tracks.append({'title': 'Target on My Back', 'length': s('4:31'), 'number': '11'})
    fs_ = files(*[(t['title'], '3:%02d' % (10 + i * 4), '') for i, t in enumerate(tracks[:10])])
    fs_ += files(('Something Else', '4:32', ''))
    res = place(fs_, tracks, similarity2)
    assert (res[-1]['track'], res[-1]['status']) == (10, ASSUMED)
    assert all(r['status'] == OK for r in res[:10])


def test_last_free_track_needs_matching_length():
    tracks = [{'title': 'A', 'length': s('3:00'), 'number': '1'}, {'title': 'B', 'length': s('4:31'), 'number': '2'}]
    res = place(files(('A', '3:00', ''), ('Zzz', '6:00', '')), tracks, similarity2)
    assert res[1]['status'] == UNPLACED


def test_no_assumption_on_an_unconfirmed_release():
    # Nothing placed by title (e.g. a wrong release): leftover lengths may coincide, but never assume.
    tracks = [{'title': 'X', 'length': s('4:31'), 'number': '1'}]
    assert place(files(('Unrelated', '4:31', '')), tracks, similarity2)[0]['status'] == UNPLACED


def test_two_leftovers_two_free_tracks_by_unique_length():
    tracks = [{'title': 'A', 'length': s('3:00'), 'number': '1'}, {'title': 'B', 'length': s('4:00'), 'number': '2'},
              {'title': 'C', 'length': s('5:00'), 'number': '3'}, {'title': 'D', 'length': s('6:00'), 'number': '4'}]
    res = place(files(('A', '3:00', ''), ('B', '4:00', ''), ('x', '6:01', ''), ('y', '5:02', '')), tracks, similarity2)
    assert [(r['track'], r['status']) for r in res[2:]] == [(3, ASSUMED), (2, ASSUMED)]


def test_dotted_acronyms_are_the_same_title():
    # Anthrax: the file says "NYC 93", MusicBrainz "N.Y.C. 93" -- a title match, not an "assumed" one.
    tracks = [{'title': 'N.Y.C. 93', 'length': s('4:49'), 'number': '7'},
              {'title': 'Everybody\u2019s Got a Plan', 'length': s('4:37'), 'number': '4'}]
    res = place(files(('NYC 93', '4:49', '7')), tracks, similarity2)
    assert (res[0]['track'], res[0]['status'], res[0]['reason']) == (0, OK, 'title+duration')


def test_spacing_only_title_difference_is_a_title_match():
    # MusicBrainz lists Anthrax's track as "NYC93"; the file says "NYC 93".
    tracks = [{'title': 'NYC93', 'length': s('4:49'), 'number': '7'},
              {'title': 'Everybody\u2019s Got a Plan', 'length': s('4:37'), 'number': '4'}]
    res = place(files(('NYC 93', '4:49', '7')), tracks, similarity2)
    assert (res[0]['track'], res[0]['status'], res[0]['reason']) == (0, OK, 'title+duration')


# -- albums that were not looked up: track numbers (numbering.py) ---------------------------------

def _plan(numbers, disc=1, total=0):
    from numbering import plan
    return plan([{'disc': disc, 'number': n, 'total': total} for n in numbers])


def test_numbering_offset_by_one_is_shifted():
    new, notes = _plan([2, 3, 4, 5, 6, 7, 8, 9])            # the ripper counted the cover as 01
    assert [new[i] for i in range(8)] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert notes[1] == ('shifted', 'tracks 02-09 renumbered to 01-08 (none missing)')


def test_numbering_clean_run_is_left_alone():
    assert _plan([1, 2, 3]) == ({}, {})


def test_numbering_undecidable_offsets_are_flagged_not_guessed():
    new, notes = _plan([3, 4, 5])                            # offset, or a rip missing 1-2?
    assert new == {} and notes[1][0] == 'problem' and 'tracks 1-2 missing' in notes[1][1]
    new, notes = _plan([2, 3, 4, 5, 6, 7, 8, 9], total=9)    # the tag says 9 tracks: track 1 IS missing
    assert new == {} and 'track 1 missing' in notes[1][1]
    new, _ = _plan([3, 4, 5], total=3)                       # the tag says 3 tracks: an offset
    assert new == {0: 1, 1: 2, 2: 3}


def test_numbering_gaps_duplicates_and_missing_numbers_are_reported():
    assert _plan([1, 2, 4])[1][1] == ('problem', 'missing track number 3 (numbers run 1..4)')
    assert _plan([1, 2, 2])[1][1] == ('problem', 'track number 2 used more than once')
    assert _plan([1, 0, 3])[1][1] == ('problem', '1 file(s) have no track number')
    assert _plan([2, 4, 5])[0] == {}                         # gaps are never shifted


def test_numbering_per_disc():
    from numbering import plan
    items = [{'disc': 1, 'number': n, 'total': 0} for n in (1, 2)] + \
            [{'disc': 2, 'number': n, 'total': 0} for n in (2, 3)]
    new, notes = plan(items)
    assert new == {2: 1, 3: 2} and notes[2][1].startswith('disc 2: tracks 02-03') and 1 not in notes


def test_numbering_reads_n_of_total():
    from numbering import number
    assert (number('3/9'), number('03'), number(''), number('x')) == (3, 3, 0, 0)


def test_last_free_track_by_title_when_the_source_length_is_off():
    # Abort to Be Born "Misanformic": MA lists "Endless Lust and Greed" as 4:24, the file is 4:42.
    tracks = [{'title': 'Song %d' % i, 'length': s('3:%02d' % (10 + i * 4)), 'number': str(i + 1)} for i in range(7)]
    tracks.insert(4, {'title': 'Endless Lust and Greed', 'length': s('4:24'), 'number': '5'})
    fs_ = files(*[(t['title'], '3:%02d' % (10 + i * 4), '') for i, t in enumerate(tracks) if i != 4])
    fs_ += files(('Endless Lust And Greed', '4:42', '5'))
    res = place(fs_, tracks, similarity2)
    assert (res[-1]['track'], res[-1]['status']) == (4, ASSUMED)
    assert '4:42 vs 4:24' in res[-1]['reason']


def test_title_with_wrong_length_is_not_assumed_while_other_tracks_are_free_by_title():
    tracks = [{'title': 'A', 'length': s('3:00'), 'number': '1'}, {'title': 'B', 'length': s('4:00'), 'number': '2'}]
    res = place(files(('A', '3:00', ''), ('A', '5:00', '')), tracks, similarity2)
    assert res[1]['status'] == UNPLACED


def test_fingerprint_placement_does_not_wait_forever(monkeypatch):
    # Audit part 2 M3: a file fpcalc cannot read never gets a callback, so nothing was ever placed.
    from contextlib import nullcontext
    from types import SimpleNamespace
    plugin = _load_tracks_plugin()
    messages, resolved = [], []

    class Unmatched:
        def iterfiles(self):
            return iter(['good', 'bad'])
    album = SimpleNamespace(id='a', unmatched_files=Unmatched(), metadata={'album': 'X'})
    window = SimpleNamespace(metadata_box=SimpleNamespace(ignore_updates=nullcontext()),
                             set_statusbar_message=lambda msg, *a: messages.append(msg % a))
    monkeypatch.setattr(plugin, '_api', SimpleNamespace(tagger=SimpleNamespace(albums={'a': album}, window=window)))
    monkeypatch.setattr(plugin, 'resolve', lambda album, files: resolved.append(list(files)))
    good, bad = type('F', (), {})(), type('F', (), {})()
    run = {'pending': {good, bad}, 'done': False}
    plugin._fingerprinted(album, good, run, result={'recordings': [{'id': 'r1'}]})
    assert not resolved                                  # still waiting for "bad"
    plugin._fp_resolve(album, run)                       # the timeout
    assert len(resolved) == 1 and '1 could not be fingerprinted' in messages[-1]
    plugin._fingerprinted(album, bad, run)               # a late answer: placed only once
    assert len(resolved) == 1

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
            tagger=self.tagger, logger=types.SimpleNamespace(debug=lambda *a, **k: None))
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
        self.assertIs(c.parent_item, self.album.unmatched_files)
        self.assertEqual(a.metadata['~placement'], 'renumbered')
        self.assertEqual(a.metadata['tracknumber'], '5')                      # takes the track's number
        self.assertEqual(b.metadata['~placement'], '')
        self.assertEqual(c.metadata['~placement'], 'unplaced')
        self.assertIn('audio is 3:24 vs 5:30', c.metadata['~placement_reason'])

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

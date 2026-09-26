# SPDX-License-Identifier: GPL-2.0-or-later
# The album cover: the better of Metal Archives' and MusicBrainz' (user, 2026-09-26).
import importlib.util
import io
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location('mltest_covers', Path(__file__).resolve().parents[1] / 'metallib_ma' / 'covers.py')
covers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(covers)


def test_absurd_metal_archives_cover_beats_the_small_one():
    assert covers.best({'MusicBrainz': (300, 297, 29749), 'Metal Archives': (1000, 1000, 250000)}) == 'Metal Archives'


def test_square_first_then_bigger_up_to_the_cap_then_the_smaller_file():
    assert covers.best({'a': (1400, 1000, 1), 'b': (800, 800, 1)}) == 'b'              # square beats bigger
    assert covers.best({'a': (1200, 1200, 5), 'b': (600, 600, 1)}) == 'a'              # bigger
    assert covers.best({'a': (3000, 3000, 9_000_000), 'b': (1600, 1600, 900_000)}) == 'b'   # past the cap: lighter
    assert covers.best({'a': (0, 0, 10)}) is None                                       # unreadable


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_caa_front_takes_the_1200_version():
    listing = {'images': [{'front': False, 'image': 'http://x/back.jpg'},
                          {'front': True, 'image': 'http://x/orig.jpg', 'thumbnails': {'1200': 'http://x/1200.jpg'}}]}
    asked = []

    def opener(req, timeout=None):
        asked.append(req.full_url)
        return _Resp(json.dumps(listing).encode() if req.full_url.endswith('/') else b'IMG')
    assert covers.caa_front('rel-1', opener) == (b'IMG', 'http://x/1200.jpg')
    assert asked == ['https://coverartarchive.org/release/rel-1/', 'https://x/1200.jpg']


def test_caa_without_cover_art():
    def opener(req, timeout=None):
        raise OSError('404')
    assert covers.caa_front('rel-1', opener) is None

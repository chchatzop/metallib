# SPDX-License-Identifier: GPL-2.0-or-later
# Step 3: the per-field rule that fills New Value from the MusicBrainz / Metal Archives columns.
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_ma'))
from rules import (  # noqa: E402
    MA,
    MB,
    choose,
)


def test_user_list_ma_first():
    for tag in ('title', 'album', 'releasetype', 'genre', 'label', 'catalognumber', 'media'):
        assert choose(tag, {MB: ['mb'], MA: ['ma']}) == (MA, ['ma']), tag


def test_lineup_credits_ma_first():
    assert choose('performer:guitar', {MB: ['x'], MA: ['Archaon']}) == (MA, ['Archaon'])
    assert choose('lyricist', {MB: ['x'], MA: ['Destroyer']}) == (MA, ['Destroyer'])


def test_mb_only_things_mb_first():
    for tag in ('barcode', 'isrc', 'asin', 'releasecountry', 'musicbrainz_albumid'):
        assert choose(tag, {MB: ['mb'], MA: ['ma']}) == (MB, ['mb']), tag


def test_falls_back_to_whichever_has_it():
    assert choose('title', {MB: ['only mb'], MA: []}) == (MB, ['only mb'])
    assert choose('barcode', {MB: [], MA: ['only ma']}) == (MA, ['only ma'])
    assert choose('comment', {MB: [], MA: []}) is None


def test_dates_most_precise_wins_then_ma():
    assert choose('date', {MB: ['1996-03-12'], MA: ['1996']}) == (MB, ['1996-03-12'])
    assert choose('date', {MB: ['1996'], MA: ['1996-04-30']}) == (MA, ['1996-04-30'])
    assert choose('date', {MB: ['1996-03-12'], MA: ['1996-04-30']}) == (MA, ['1996-04-30'])

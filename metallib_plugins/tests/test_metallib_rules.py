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


def test_original_date_is_metal_archives_else_the_earliest_year():
    from rules import DG
    # Metal Archives' original date whenever it has one (user) -- even when MB's is more precise.
    assert choose('originaldate', {MB: ['2021-05-01'], MA: ['1995']}) == (MA, ['1995'])
    assert choose('originaldate', {MB: ['1995-03-12'], MA: ['1995']}) == (MA, ['1995'])
    assert choose('originalyear', {MB: ['1994'], MA: ['1995']}) == (MA, ['1995'])
    # Without MA: the earliest year, the most precise within it.
    assert choose('originaldate', {MB: ['1995-03-12'], DG: ['1990']}) == (MB, ['1995-03-12'])
    # Discogs dates are a pressing's: only when neither MA nor MB has one.
    assert choose('originaldate', {DG: ['1990-01-01'], MA: ['1995']}) == (MA, ['1995'])
    assert choose('originaldate', {DG: ['1990']}) == (DG, ['1990'])


def test_track_position_never_comes_from_a_source_column():
    # A 10-track vinyl in the MA column must not renumber an 11-track CD.
    for tag in ('tracknumber', 'totaltracks', 'discnumber', 'totaldiscs'):
        assert choose(tag, {MB: ['7'], MA: ['6']}) is None, tag


def test_pressing_tags_the_library_way():
    # user: A.D.N. 1988, the 2010 "Remastered, limited edition" CD -> edition Lim, remaster 2010
    import importlib.util
    from picard.metadata import Metadata
    spec = importlib.util.spec_from_file_location(
        'mltest_edition', Path(__file__).resolve().parents[1] / 'metallib_ma' / 'edition.py')
    ed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ed)
    adn = Metadata({'~releasecomment': 'Remastered, limited edition', 'media': 'CD', 'date': '2010',
                    'originalyear': '1988', 'releasecountry': 'ES'})
    assert ed.derive(adn) == {'edition': ['Lim'], 'remaster': '2010'}
    jap = Metadata({'~releasecomment': 'Limited edition', '~pressingcountry': 'JP', 'releasecountry': 'NO',
                    'date': '1996-05-01', 'originaldate': '1996-02-01'})
    assert ed.derive(jap) == {'edition': ['Jap', 'Lim']}                    # same year: no RE
    re_ = Metadata({'date': '2004', 'originalyear': '1996', 'media': 'CD; Digipak'})
    assert ed.derive(re_) == {'reissue': '2004'}
    assert ed.derive(Metadata({'date': '1997', 'originalyear': '1996'})) == {}   # one year later: not a reissue

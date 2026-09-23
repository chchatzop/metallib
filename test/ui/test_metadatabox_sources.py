# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
#
# Copyright (C) 2026 MetalLib contributors
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""MetalLib: per-source columns in the metadata box (picard/ui/metadatabox/sources.py)."""

from PyQt6 import QtWidgets

from test.picardtestcase import PicardTestCase

from picard.album import Album
from picard.file import File
from picard.metadata import Metadata
from picard.track import Track

from picard.ui.metadatabox import (
    MetadataBox,
    apply_tag_values,
)
from picard.ui.metadatabox import sources
from picard.ui.metadatabox.tagdiff import TagDiff


def md(**tags):
    m = Metadata()
    for tag, value in tags.items():
        m[tag] = value
    return m


class TestSources(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.album = Album('metallib-ma-1')

    def _track_with_file(self, n, mb, ma):
        track = Track('t%d' % n, self.album)
        track.source_metadata = {'MusicBrainz': mb, 'Metal Archives': ma}
        f = File('f%d.flac' % n)
        track.files.append(f)
        f.parent_item = track
        return track, f

    def test_file_falls_back_to_its_track(self):
        track, f = self._track_with_file(1, md(title='A'), md(title='B'))
        self.assertIs(sources.object_sources(f), track.source_metadata)

    def test_collect_single(self):
        _, f = self._track_with_file(1, md(title='Obnoxious', date='1990-04'), md(title='Obnoxious', date='1990-04-30'))
        got = sources.collect([f], ['title', 'date', 'barcode'])
        self.assertEqual(list(got), ['MusicBrainz', 'Metal Archives'])
        self.assertEqual(got['MusicBrainz'], {'title': ['Obnoxious'], 'date': ['1990-04'], 'barcode': []})
        self.assertEqual(got['Metal Archives']['date'], ['1990-04-30'])

    def test_collect_multiple_marks_disagreement(self):
        _, f1 = self._track_with_file(1, md(album='Obnoxious', title='One'), md(album='Obnoxious'))
        _, f2 = self._track_with_file(2, md(album='Obnoxious', title='Two'), md(album='Obnoxious'))
        got = sources.collect([f1, f2], ['album', 'title'])
        self.assertEqual(got['MusicBrainz']['album'], ['Obnoxious'])
        self.assertIs(got['MusicBrainz']['title'], sources.DIFFERENT)

    def test_no_sources_no_columns(self):
        self.assertEqual(sources.collect([File('x.flac')], ['title']), {})

    def test_use_source_value_is_per_object_and_recorded(self):
        _, f1 = self._track_with_file(1, md(title='One (MB)'), md(title='One'))
        _, f2 = self._track_with_file(2, md(title='Two (MB)'), md(title='Two'))
        changed = sources.use_source_value([f1, f2], 'Metal Archives', 'title', apply_tag_values)
        self.assertEqual(set(changed), {f1, f2})
        self.assertEqual((f1.metadata['title'], f2.metadata['title']), ('One', 'Two'))
        self.assertEqual(f1.value_sources, {'title': 'Metal Archives'})

    def test_use_source_value_skips_objects_without_it(self):
        _, f1 = self._track_with_file(1, md(title='One (MB)'), md())
        f1.metadata['title'] = 'keep'
        self.assertEqual(sources.use_source_value([f1], 'Metal Archives', 'title', apply_tag_values), [])
        self.assertEqual(f1.metadata['title'], 'keep')


class _Box(QtWidgets.QTableWidget):
    """A real Qt table running MetadataBox's own source-column methods."""

    COLUMN_TAG = MetadataBox.COLUMN_TAG
    COLUMN_ORIG = MetadataBox.COLUMN_ORIG
    COLUMN_NEW = MetadataBox.COLUMN_NEW
    _set_source_columns = MetadataBox._set_source_columns
    _source_of_column = MetadataBox._source_of_column
    _fill_source_cells = MetadataBox._fill_source_cells

    def __init__(self):
        super().__init__(1, 3)
        self._source_names = []

    def get_item(self, row, column):
        item = self.item(row, column)
        if not item:
            item = QtWidgets.QTableWidgetItem()
            self.setItem(row, column, item)
        return item


class TestSourceColumns(PicardTestCase):
    def test_columns_follow_the_sources_and_cells_show_values(self):
        box = _Box()
        tag_diff = TagDiff()
        tag_diff.add('date', old=['1990'], new=['1990-04-30'])
        tag_diff.update_tag_names()
        tag_diff.sources = {'MusicBrainz': {'date': ['1990-04']}, 'Metal Archives': {'date': ['1990-04-30']}}
        box.tag_diff = tag_diff
        box._set_source_columns(list(tag_diff.sources))
        box._fill_source_cells(0, 'date', box.get_item, False)

        self.assertEqual(box.columnCount(), 5)
        self.assertEqual([box.horizontalHeaderItem(c).text() for c in (3, 4)], ['MusicBrainz', 'Metal Archives'])
        self.assertEqual((box.item(0, 3).text(), box.item(0, 4).text()), ('1990-04', '1990-04-30'))
        self.assertEqual((box._source_of_column(3), box._source_of_column(4), box._source_of_column(2)),
                         ('MusicBrainz', 'Metal Archives', None))
        # MB disagrees with New Value -> highlighted; MA equals it -> normal text colour
        self.assertNotEqual(box.item(0, 3).foreground().color(), box.item(0, 4).foreground().color())

        box._set_source_columns([])
        self.assertEqual(box.columnCount(), 3)

    def test_different_values_placeholder(self):
        box = _Box()
        tag_diff = TagDiff()
        tag_diff.add('title', old=['x'], new=['x'])
        tag_diff.update_tag_names()
        tag_diff.sources = {'MusicBrainz': {'title': sources.DIFFERENT}}
        box.tag_diff = tag_diff
        box._set_source_columns(['MusicBrainz'])
        box._fill_source_cells(0, 'title', box.get_item, False)
        self.assertEqual(box.item(0, 3).text(), '(different values)')


class TestSourceRowsStay(PicardTestCase):
    """A tag a source knows keeps its row after the user deletes it, so it can be taken back."""

    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')

    def test_source_tag_names(self):
        track = Track('t1', Album('a'))
        track.source_metadata = {'Metal Archives': md(catalognumber='MIM7324-2CD', **{'~ma_album_id': '1'}),
                                 'MusicBrainz': md(barcode='123')}
        self.assertEqual(sources.source_tag_names([track]), {'catalognumber', 'barcode'})

    def test_deleted_added_tag_still_has_a_row(self):
        tag_diff = TagDiff()
        tag_diff.add('title', old=['x'], new=['x'])
        # catalognumber was only added by the lookup and then deleted: neither old nor new has it.
        tag_diff.extra_tags = {'catalognumber'}
        tag_diff.update_tag_names()
        self.assertIn('catalognumber', tag_diff.tag_names)

    def test_without_sources_nothing_extra(self):
        tag_diff = TagDiff()
        tag_diff.add('title', old=['x'], new=['x'])
        tag_diff.update_tag_names()
        self.assertEqual(tag_diff.tag_names, ['title'])

    def test_taking_a_deleted_value_back(self):
        track = Track('t1', Album('a'))
        track.source_metadata = {'Metal Archives': md(catalognumber='MIM7324-2CD')}
        f = File('f.flac')
        track.files.append(f)
        f.parent_item = track
        f.metadata['catalognumber'] = 'MIM7324-2CD'
        del f.metadata['catalognumber']                       # the user pressed Delete
        self.assertNotIn('catalognumber', f.metadata)
        sources.use_source_value([f], 'Metal Archives', 'catalognumber', apply_tag_values)
        self.assertEqual(f.metadata['catalognumber'], 'MIM7324-2CD')
        self.assertNotIn('catalognumber', f.metadata.deleted_tags)


class TestCopyFromSourceColumns(PicardTestCase):
    """Ctrl+C on a MusicBrainz / Metal Archives cell crashed with KeyError: 3 (user report)."""

    class _Clipboard:
        text = None

        def setText(self, text):
            self.text = text

    def _box(self):
        box = _Box()
        box._copy_single_item = MetadataBox._copy_single_item.__get__(box)
        box._get_row_info = MetadataBox._get_row_info.__get__(box)
        tag_diff = TagDiff()
        tag_diff.add('artist', old=['Iron Curtain'], new=['Iron Curtain'])
        tag_diff.update_tag_names()
        tag_diff.sources = {'MusicBrainz': {'artist': ['Iron Curtain (ES)']}, 'Metal Archives': {'artist': sources.DIFFERENT}}
        box.tag_diff = tag_diff
        box._set_source_columns(list(tag_diff.sources))
        for c in range(5):
            box.get_item(0, c)
        box.clipboard = self._Clipboard()
        box.tagger = type('T', (), {'clipboard': lambda s: box.clipboard})()
        return box

    def test_copy_source_cell(self):
        box = self._box()
        box.setCurrentCell(0, 3)
        box._copy_single_item()
        self.assertEqual(box.clipboard.text, 'Iron Curtain (ES)')

    def test_copy_differing_source_cell_copies_nothing(self):
        box = self._box()
        box.setCurrentCell(0, 4)
        box._copy_single_item()
        self.assertIsNone(box.clipboard.text)

    def test_copy_new_value_still_works(self):
        box = self._box()
        box.setCurrentCell(0, 2)
        box._copy_single_item()
        self.assertEqual(box.clipboard.text, 'Iron Curtain')

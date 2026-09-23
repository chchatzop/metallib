# SPDX-License-Identifier: GPL-2.0-or-later
# Undo journal on REAL audio files (generated with ffmpeg), saved through Picard's own writers.
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import (
    MagicMock,
    patch,
)

import pytest

from test.picardtestcase import PicardTestCase

from picard.config import Option
import picard.options  # noqa: F401 -- registers every option with its default


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_undo'))
import undo_store  # noqa: E402


FFMPEG = shutil.which('ffmpeg')
pytestmark = pytest.mark.skipif(not FFMPEG, reason='ffmpeg is needed to generate test audio')


def _make_audio(folder, name):
    path = os.path.join(folder, name)
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-y', path],
                   check=True)
    return path


def _audio_md5(path):
    out = subprocess.run([FFMPEG, '-v', 'error', '-i', path, '-map', '0:a', '-f', 'md5', '-'],
                         check=True, capture_output=True, text=True).stdout
    return out.strip()


def _png():
    # 1x1 PNG
    return bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                         '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')


def _tag_flac(path):
    from mutagen.flac import (
        FLAC,
        Picture,
    )
    f = FLAC(path)
    f['TITLE'] = 'Original Title'
    f['ARTIST'] = 'Original Artist'
    f['TRACKNUMBER'] = '3'
    f['COMMENT'] = 'keep me'
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, 'image/png', _png()
    f.add_picture(pic)
    f.save()


def _tag_mp3(path):
    from mutagen.id3 import (
        APIC,
        COMM,
        ID3,
        TIT2,
        TPE1,
        TRCK,
    )
    t = ID3()
    t.add(TIT2(encoding=3, text='Original Title'))
    t.add(TPE1(encoding=3, text='Original Artist'))
    t.add(TRCK(encoding=3, text='3'))
    t.add(COMM(encoding=3, lang='eng', desc='', text='keep me'))
    t.add(APIC(encoding=3, mime='image/png', type=3, desc='', data=_png()))
    t.save(path, v2_version=3, v1=2)          # ID3v2.3 + an ID3v1 tag, like many old rips


class TestUndoAgainstPicardSaves(PicardTestCase):
    def setUp(self):
        super().setUp()
        settings = {name: opt.default for (section, name), opt in Option.registry.items() if section == 'setting'}
        settings['clear_existing_tags'] = True          # the harshest save: wipes every old tag
        self.set_config_values(setting=settings)
        patcher = patch('picard.item.tagger_instance', return_value=MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = self.mktmpdir() if hasattr(self, 'mktmpdir') else self._tmp()

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp(prefix='metallib_undo_')
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _picard_save(self, cls, path):
        from picard.metadata import Metadata
        md = Metadata()
        md.update({'title': 'New Title', 'artist': 'New Artist', 'tracknumber': '5', 'totaltracks': '9',
                   'discnumber': '1', 'totaldiscs': '1'})
        cls(path)._save(path, md)

    def test_flac_tags_and_pictures_come_back(self):
        from mutagen.flac import FLAC

        from picard.formats.vorbis import FLACFile
        path = _make_audio(self.dir, 't.flac')
        _tag_flac(path)
        before = FLAC(path)
        before_tags, before_pics = list(before.tags), [p.write() for p in before.pictures]
        audio = _audio_md5(path)

        journal = undo_store.UndoJournal(os.path.join(self.dir, 'journal'))
        self.addCleanup(journal.close)
        entry = journal.record(path)
        self._picard_save(FLACFile, path)
        journal.saved(entry, path)
        after = FLAC(path)
        self.assertEqual(after['TITLE'], ['New Title'])
        self.assertEqual(after.pictures, [])                       # clear_existing_tags removed it

        results = journal.undo_batch(journal.batches()[0][0])
        self.assertTrue(all(ok for _, ok, _ in results), results)
        restored = FLAC(path)
        self.assertEqual(list(restored.tags), before_tags)
        self.assertEqual([p.write() for p in restored.pictures], before_pics)
        self.assertEqual(_audio_md5(path), audio)
        self.assertEqual(journal.batches(), [])                    # nothing left to undo

    def test_mp3_is_byte_identical_after_undo(self):
        from picard.formats.id3 import MP3File
        path = _make_audio(self.dir, 't.mp3')
        _tag_mp3(path)
        original = Path(path).read_bytes()

        journal = undo_store.UndoJournal(os.path.join(self.dir, 'journal'))
        self.addCleanup(journal.close)
        entry = journal.record(path)
        self._picard_save(MP3File, path)
        journal.saved(entry, path)
        self.assertNotEqual(Path(path).read_bytes(), original)

        results = journal.undo_batch(journal.batches()[0][0])
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                         hashlib.sha256(original).hexdigest())

    def test_rename_and_companion_files_are_moved_back(self):
        from picard.formats.vorbis import FLACFile
        old_dir = os.path.join(self.dir, 'Band - Album (old name)')
        os.makedirs(old_dir)
        path = _make_audio(old_dir, '01 old.flac')
        _tag_flac(path)
        Path(old_dir, 'cover.jpg').write_bytes(b'jpg')

        journal = undo_store.UndoJournal(os.path.join(self.dir, 'journal'))
        self.addCleanup(journal.close)
        entry = journal.record(path)
        self._picard_save(FLACFile, path)
        # What Picard's rename + "move additional files" + "delete empty dirs" would do:
        new_dir = os.path.join(self.dir, 'Band', '2020 - Album [16-44]')
        os.makedirs(new_dir)
        new_path = os.path.join(new_dir, 'Band - Album - 05 - New Title.flac')
        shutil.move(path, new_path)
        shutil.move(os.path.join(old_dir, 'cover.jpg'), os.path.join(new_dir, 'cover.jpg'))
        os.rmdir(old_dir)
        journal.saved(entry, new_path)

        results = journal.undo_batch(journal.batches()[0][0])
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(old_dir, 'cover.jpg')))
        self.assertFalse(os.path.exists(new_path))
        from mutagen.flac import FLAC
        self.assertEqual(FLAC(path)['TITLE'], ['Original Title'])

    def test_undo_never_overwrites_a_taken_path(self):
        from picard.formats.vorbis import FLACFile
        path = _make_audio(self.dir, 'a.flac')
        _tag_flac(path)
        journal = undo_store.UndoJournal(os.path.join(self.dir, 'journal'))
        self.addCleanup(journal.close)
        entry = journal.record(path)
        self._picard_save(FLACFile, path)
        new_path = os.path.join(self.dir, 'b.flac')
        shutil.move(path, new_path)
        journal.saved(entry, new_path)
        _make_audio(self.dir, 'a.flac')                             # something else took the old name
        taken = Path(path).read_bytes()

        (eid, ok, msg), = journal.undo_batch(journal.batches()[0][0])
        self.assertFalse(ok)
        self.assertIn('is taken', msg)
        self.assertEqual(Path(path).read_bytes(), taken)             # untouched
        from mutagen.flac import FLAC
        self.assertEqual(FLAC(new_path)['TITLE'], ['Original Title'])   # but its tags are back


def test_batches_group_by_time(tmp_path):
    clock = [1000.0]
    j = undo_store.UndoJournal(str(tmp_path / 'j'), clock=lambda: clock[0])
    files = []
    for i in range(3):
        p = tmp_path / ('f%d.bin' % i)
        p.write_bytes(b'x' * 10)
        files.append(str(p))
    e1 = j.record(files[0])
    clock[0] += 5
    e2 = j.record(files[1])
    clock[0] += 100
    e3 = j.record(files[2])
    for e, f in ((e1, files[0]), (e2, files[1]), (e3, files[2])):
        j.saved(e, f)
    assert [len(entries) for _, _, entries in j.batches()] == [1, 2]     # newest batch first
    j.close()


# --- The plugin's wrappers around Picard's real File.save() ------------------------------------

def _load_undo_plugin():
    import importlib.util
    d = Path(__file__).resolve().parent.parent / 'metallib_undo'
    spec = importlib.util.spec_from_file_location('picard.plugins.metallib_undo_test', d / '__init__.py',
                                                  submodule_search_locations=[str(d)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestUndoPluginWrapsPicardSave(TestUndoAgainstPicardSaves):
    def setUp(self):
        super().setUp()
        import types

        from picard.file import File
        self.plugin = _load_undo_plugin()
        self.plugin._api = types.SimpleNamespace(logger=MagicMock())
        self.plugin._journal = undo_store.UndoJournal(os.path.join(self.dir, 'journal'))
        self.addCleanup(self.plugin._journal.close)
        self.plugin._originals.update(save=File.save, saving_finished=File._saving_finished)
        wrap = patch.object(File, '_saving_finished', self.plugin._saving_finished)   # as enable() does
        wrap.start()
        self.addCleanup(wrap.stop)
        # Run Picard's save task synchronously, callback included.
        sync = patch('picard.file.thread.run_task',
                     side_effect=lambda func, next_func=None, **kw: next_func(result=func()) if next_func else func())
        sync.start()
        self.addCleanup(sync.stop)

    def _file(self, path):
        from picard.formats.vorbis import FLACFile
        f = FLACFile(path)
        f.tagger.stopping = False                       # the patched tagger_instance() mock
        f.tagger._saving_files_count = 0
        f.state = f.State.NORMAL
        f.metadata.update({'title': 'New Title', 'artist': 'New Artist'})
        return f

    def test_save_is_journaled_and_undoable(self):
        from mutagen.flac import FLAC
        path = _make_audio(self.dir, 's.flac')
        _tag_flac(path)
        f = self._file(path)
        self.plugin._save(f)                                   # plugin's File.save
        self.assertEqual(FLAC(path)['TITLE'], ['New Title'])
        (batch, _, entries), = self.plugin._journal.batches()
        self.assertEqual(entries[0]['new_path'], path)
        self.plugin._journal.undo_batch(batch)
        self.assertEqual(FLAC(path)['TITLE'], ['Original Title'])

    def test_no_snapshot_no_save(self):
        from mutagen.flac import FLAC
        path = _make_audio(self.dir, 'n.flac')
        _tag_flac(path)
        f = self._file(path)
        with patch.object(self.plugin._journal, 'record', side_effect=OSError('share offline')):
            self.plugin._save(f)
        self.assertEqual(FLAC(path)['TITLE'], ['Original Title'])     # untouched
        self.assertEqual(f.state, f.State.ERROR)
        self.assertIn('could not be backed up', f.errors[0])

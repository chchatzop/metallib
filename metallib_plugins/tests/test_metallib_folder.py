# SPDX-License-Identifier: GPL-2.0-or-later
# Folder contents: verdicts, the cover rename, and reversible trash moves (on a temp "drive").
import os
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_folder'))
import folder_scan as fs  # noqa: E402


def _album(tmp_path):
    d = tmp_path / 'Acid_Reign-Obnoxious-(CDFLAG39)-CD-FLAC-1990-OCCiPiTAL'
    d.mkdir()
    for n in ('01-acid_reign-creative_restraint.flac', '02-acid_reign-joke_chain.flac'):
        (d / n).write_bytes(b'f' * 5000)
    (d / '00-acid_reign-obnoxious.sfv').write_text('01-acid_reign-creative_restraint.flac 1234ABCD\n'
                                                    '02-acid_reign-joke_chain.flac 1234ABCD\n')
    (d / '00-acid_reign-obnoxious.nfo').write_text('nfo')
    (d / '00-acid_reign-obnoxious.m3u').write_text('m3u')
    (d / 'empty.flac').write_bytes(b'')
    (d / 'sample.flac').write_bytes(b's' * 3000)               # extra audio, not in the .sfv
    (d / '00-acid_reign-obnoxious-cd-flac-1990.jpg').write_bytes(b'jpg')
    return d


def test_verdicts(tmp_path):
    d = _album(tmp_path)
    used = [str(d / '01-acid_reign-creative_restraint.flac'), str(d / '02-acid_reign-joke_chain.flac')]
    got = {e['name']: e['verdict'] for e in fs.scan([str(d)], used, 'Acid Reign', 'Obnoxious')}
    assert got == {'00-acid_reign-obnoxious.m3u': 'junk', '00-acid_reign-obnoxious.nfo': 'junk',
                   '00-acid_reign-obnoxious.sfv': 'junk', 'empty.flac': 'junk', 'sample.flac': 'check',
                   '00-acid_reign-obnoxious-cd-flac-1990.jpg': 'rename'}


def test_cover_target_and_summary(tmp_path):
    d = _album(tmp_path)
    entries = fs.scan([str(d)], [], 'Acid Reign', 'Obnoxious')
    img = [e for e in entries if e['verdict'] == fs.RENAME][0]
    assert os.path.basename(img['target']) == 'Acid Reign - Obnoxious - 00 - Front.jpg'
    assert fs.summary(entries).startswith('4 junk')


def test_clean_folder(tmp_path):
    d = tmp_path / 'clean'
    d.mkdir()
    (d / 'a.flac').write_bytes(b'x' * 100)
    (d / 'Band - Album - 00 - Front.jpg').write_bytes(b'x')
    assert fs.summary(fs.scan([str(d)], [str(d / 'a.flac')], 'Band', 'Album')) == 'clean'


def test_stale_sfv_is_not_trusted(tmp_path):
    d = tmp_path / 'a'
    d.mkdir()
    for i in range(5):
        (d / ('%02d.flac' % i)).write_bytes(b'x' * 100)
    (d / 'old.sfv').write_text('someotherfile.flac 00000000\n')
    reasons = {e['reason'] for e in fs.scan([str(d)], [], 'B', 'A') if e['verdict'] == fs.CHECK}
    assert reasons == {'audio not used by this album in MetalLib'}       # not "not in the .sfv"


def test_trash_and_rename_are_undone(tmp_path, monkeypatch):
    d = _album(tmp_path)
    monkeypatch.setattr(fs, 'trash_root', lambda p: str(tmp_path))      # the temp dir plays the drive root
    log = fs.ActionLog(str(tmp_path / 'log.jsonl'))
    batch = fs.new_batch()
    nfo = str(d / '00-acid_reign-obnoxious.nfo')
    moved = fs.move_to_trash(nfo, log, batch)
    assert not os.path.exists(nfo) and os.path.exists(moved) and fs.TRASH_DIR in moved
    img = str(d / '00-acid_reign-obnoxious-cd-flac-1990.jpg')
    fs.rename(img, str(d / 'Acid Reign - Obnoxious - 00 - Front.jpg'), log, batch)
    assert log.last_batch() == batch
    assert all(ok for ok, _ in log.undo(batch))
    assert os.path.exists(nfo) and os.path.exists(img)
    assert log.last_batch() is None


def test_undo_never_overwrites(tmp_path, monkeypatch):
    d = _album(tmp_path)
    monkeypatch.setattr(fs, 'trash_root', lambda p: str(tmp_path))
    log = fs.ActionLog(str(tmp_path / 'log.jsonl'))
    batch = fs.new_batch()
    nfo = str(d / '00-acid_reign-obnoxious.nfo')
    fs.move_to_trash(nfo, log, batch)
    Path(nfo).write_text('new file with the same name')
    (ok, msg), = log.undo(batch)
    assert not ok and 'taken' in msg
    assert Path(nfo).read_text() == 'new file with the same name'


def test_trash_root():
    assert fs.trash_root(r'H:\1 New\x\y.nfo') == 'H:' + os.sep
    unc = '\\\\NAStradamus\\data\\usenet\\x.nfo'
    assert fs.trash_root(unc).lower() == '\\\\nastradamus\\data'


def test_existing_cover_names_are_kept():
    assert fs.is_front_name('Aborym - Fire Walk with Us [SC 022-2 CD] - 00 - Front.jpg')
    assert fs.is_front_name('Alestorm - Curse of the Crystal Coconut (Del. Ed.) - 0-00 - Front.jpg')
    assert not fs.is_front_name('00-acweald-archaic-tape-2025.jpg')


def test_cover_name_follows_the_track_names(tmp_path):
    d = tmp_path / 'x'
    d.mkdir()
    tracks = [d / ('Aborym - Fire Walk with Us [SC 022-2 CD] - 0%d - Song %d.flac' % (i, i)) for i in (1, 2)]
    for t in tracks:
        t.write_bytes(b'x' * 100)
    (d / 'folder.jpg').write_bytes(b'j')
    img = [e for e in fs.scan([str(d)], [str(t) for t in tracks], 'Aborym', 'Fire Walk with Us')
           if e['verdict'] == fs.RENAME][0]
    assert os.path.basename(img['target']) == 'Aborym - Fire Walk with Us [SC 022-2 CD] - 00 - Front.jpg'


def test_any_clean_up_of_this_album_can_be_undone(tmp_path, monkeypatch):
    # Audit part 2 M5: a batch that could not be fully undone kept coming back as "the last one",
    # hiding the older ones; and the undo took whichever album's clean-up was last.
    a, b = tmp_path / 'A', tmp_path / 'B'
    a.mkdir()
    b.mkdir()
    for p in (a / 'a.nfo', a / 'b.nfo', b / 'c.nfo'):
        p.write_text('x')
    log = fs.ActionLog(str(tmp_path / 'log.jsonl'))
    monkeypatch.setattr(fs, 'trash_root', lambda path: str(tmp_path))
    fs.move_to_trash(str(a / 'a.nfo'), log, '20260926-100000-0001')
    fs.move_to_trash(str(a / 'b.nfo'), log, '20260926-110000-0002')
    fs.move_to_trash(str(b / 'c.nfo'), log, '20260926-120000-0003')
    assert [bt for bt, _ in log.batches([str(a)])] == ['20260926-110000-0002', '20260926-100000-0001']
    (a / 'b.nfo').write_text('taken again')                          # the newer one cannot come back
    assert not log.undo('20260926-110000-0002')[0][0]
    assert log.undo('20260926-100000-0001') == [(True, 'a.nfo')]      # the older one still can
    assert (a / 'a.nfo').exists() and not (b / 'c.nfo').exists()      # album B untouched

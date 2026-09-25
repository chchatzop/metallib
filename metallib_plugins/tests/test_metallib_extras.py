# SPDX-License-Identifier: GPL-2.0-or-later
# Extra files: names the library's way, defaults, prefix from the tracks.
import importlib.util
import os
from pathlib import Path
import sys

import pytest


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_folder'


def _mod():
    name = 'picard.plugins.metallib_folder_xtest'
    if name + '.extras' not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / '__init__.py',
                                                      submodule_search_locations=[str(PLUGIN_DIR)])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[name] = pkg
        sub = importlib.util.spec_from_file_location(name + '.folder_scan', PLUGIN_DIR / 'folder_scan.py')
        fs = importlib.util.module_from_spec(sub)
        sys.modules[name + '.folder_scan'] = fs
        sub.loader.exec_module(fs)
        sub = importlib.util.spec_from_file_location(name + '.extras', PLUGIN_DIR / 'extras.py')
        ex = importlib.util.module_from_spec(sub)
        sys.modules[name + '.extras'] = ex
        sub.loader.exec_module(ex)
    return sys.modules[name + '.extras']


@pytest.mark.parametrize('name,stem', [
    ('folder.jpg', 'Front'), ('cover.jpg', 'Front'), ('Front Cover.png', 'Front'), ('back.jpg', 'Back'),
    ('back cover.jpg', 'Back'), ('cd.jpg', 'CD'), ('CD2.jpg', 'CD2'), ('cd1_matrix.jpg', 'CD1 Matrix'),
    ('booklet_03.jpg', 'Booklet 03'), ('scan 2.jpg', 'Booklet 02'), ('Booklet A.jpg', 'Booklet A'),
    ('jap booklet 04.jpg', 'Jap. Booklet 04'), ('inlay.jpg', 'Inlay'), ('tray.jpg', 'Inlay'), ('obi.jpg', 'OBI'),
    ('digipak inside.jpg', 'Digi Inside'), ('00-band-album-proof.jpg', 'Proof'),
    ('Abbath - Dread Reaver (Lim. Ed.) - 00 - Booklet 01.jpg', 'Booklet 01'),
    ('X - Y - 0-00 - Back.jpg', 'Back'), ('band_logo.png', ''),
])
def test_guess_stem(name, stem):
    assert _mod().guess_stem(name) == stem


def test_proofs_only_by_proof_or_prf():
    ex = _mod()
    assert ex.is_proof('00-grp-proof.jpg') and ex.is_proof('prf.jpg')
    assert not ex.is_proof('00-devoid-front.jpg')          # the group's tag is not a proof (user)


def _entries(*names):
    ex = _mod()
    return [{'path': 'C:\\a\\' + n, 'rel': n, 'folder': 'C:\\a', 'name': n, 'kind': ex.kind_of(n), 'size': 1}
            for n in names]


def test_plan_defaults():
    ex = _mod()
    got = {e['name']: (e['tick'], e['stem']) for e in ex.plan(_entries(
        'folder.jpg', 'back.jpg', 'scan_a.jpg', 'scan_b.jpg', 'proof.jpg', 'x.nfo', 'x.sfv', 'x.m3u'))}
    assert got['folder.jpg'] == (True, 'Front') and got['back.jpg'] == (True, 'Back')
    assert got['scan_a.jpg'] == (True, 'Booklet A') and got['scan_b.jpg'] == (True, 'Booklet B')
    assert got['proof.jpg'][0] is False
    assert all(got[n][0] is False for n in ('x.nfo', 'x.sfv', 'x.m3u'))


def test_lone_image_is_front_and_unnumbered_booklets_are_numbered():
    ex = _mod()
    assert [e['stem'] for e in ex.plan(_entries('00-grp-abc.jpg'))] == ['Front']
    got = [e['stem'] for e in ex.plan(_entries('booklet.jpg', 'page.jpg', 'front.jpg'))]
    assert got == ['Booklet 01', 'Booklet 02', 'Front']


def test_user_choices_and_duplicate_names():
    ex = _mod()
    es = _entries('a.jpg', 'front.jpg', 'proof.jpg')
    got = ex.plan(es, {es[0]['path']: {'stem': 'Front'}, es[2]['path']: {'tick': True, 'stem': 'Photo'}})
    assert [(e['tick'], e['stem']) for e in got] == [(True, 'Front'), (True, 'Front 2'), (True, 'Photo')]


def test_prefix_and_target_names():
    ex = _mod()
    assert ex.prefix_of('A.N.I.M.A.L. - Fin de un mundo enfermo - 01 - Solo.flac') == \
        ('A.N.I.M.A.L. - Fin de un mundo enfermo', False)
    assert ex.prefix_of('...and Oceans - The Symmetry (Lim. Ed.) - 2-05 - T.flac') == \
        ('...and Oceans - The Symmetry (Lim. Ed.)', True)
    assert ex.target_name('A - B', False, 'Front', '.JPG') == 'A - B - 00 - Front.jpg'
    assert ex.target_name('A - B', True, 'Back', '.png') == 'A - B - 0-00 - Back.png'


def test_list_extras_walks_subfolders_and_skips_album_audio(tmp_path):
    ex = _mod()
    (tmp_path / 'Scans').mkdir()
    for n in ('01.flac', 'x.nfo', 'Scans/back.jpg', 'sample.mp3'):
        (tmp_path / n).write_bytes(b'x')
    got = {e['rel'].replace('\\', '/'): e['kind'] for e in ex.list_extras([str(tmp_path)], [str(tmp_path / '01.flac')])}
    assert got == {'x.nfo': 'text', 'sample.mp3': 'other', 'Scans/back.jpg': 'image'}


def test_execute_moves_ticked_trashes_the_rest_and_removes_the_empty_folder(tmp_path):
    ex = _mod()
    fs = sys.modules['picard.plugins.metallib_folder_xtest.folder_scan']
    src, dest = tmp_path / 'in' / 'Album-GRP', tmp_path / 'out' / 'Band (NO)' / '2001 - Album [CD] [16-44]'
    (src / 'Scans').mkdir(parents=True)
    dest.mkdir(parents=True)
    for n in ('folder.jpg', 'Scans/back.jpg', '00-proof.jpg', 'album.nfo', 'album.sfv'):
        (src / n).write_bytes(b'x')
    entries = ex.plan(ex.list_extras([str(src)], []))
    log = fs.ActionLog(str(tmp_path / 'log.jsonl'))
    done = ex.execute(entries, 'Band - Album', False, str(dest), log, 'b1', fs.move_file, fs.move_to_trash)
    assert sorted(p.name for p in dest.iterdir()) == ['Band - Album - 00 - Back.jpg', 'Band - Album - 00 - Front.jpg']
    assert len(done['trashed']) == 3 and not done['errors']
    assert not src.exists()                                    # emptied source folder removed
    assert all('.metallib_trash' in t for _, t in done['trashed'])
    # everything can be put back
    assert all(ok for ok, _ in log.undo('b1'))
    assert (src / 'Scans' / 'back.jpg').exists() and (src / 'album.nfo').exists()


def test_execute_never_trashes_audio(tmp_path):
    ex = _mod()
    fs = sys.modules['picard.plugins.metallib_folder_xtest.folder_scan']
    (tmp_path / 'sample.mp3').write_bytes(b'x')
    entries = ex.plan(ex.list_extras([str(tmp_path)], []))
    assert ex.foreign_audio(entries)
    ex.execute(entries, 'A - B', False, str(tmp_path / 'o'), fs.ActionLog(str(tmp_path / 'l')), 'b', fs.move_file,
               fs.move_to_trash)
    assert (tmp_path / 'sample.mp3').exists()


def _lib():
    _mod()
    name = 'picard.plugins.metallib_folder_xtest.library'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / 'library.py')
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules[name]


def test_library_album_ident():
    lib = _lib()
    got = lib.album_ident('1996 - Opus IV (EP) (RE 2004) (Digipak) [NPR 154 CD] [16-44]')
    assert (got['year'], got['title'], got['quality']) == ('1996', 'Opus IV (EP)', '16-44')
    assert got['pressing'] == 'npr154cd|digipak|re2004'
    assert lib.album_ident('1996 - Opus IV [NPR 020 CD] [16-44]')['album'] == lib.album_ident(
        '1996 - Opus IV (RE 2004) [NPR 154 CD] [16-44]')['album']


@pytest.mark.parametrize('existing,cls', [
    ('2001 - Satanized [NPR 088 CD] [16-44]', 'collision'),
    ('2001 - Satanized  [NPR 088 CD]  [16-44]', 'collision'),
    ('2001 - Satanized [NPR088 CD] [16-44]', 'redundant'),
    ('2001 - Satanized [NPR 088 CD] [24-96]', 'quality'),
    ('2001 - Satanized (Promo) [NPR 088 CD] [16-44]', 'pressing'),
    ('2001 - Satanized (RE 2010) [NPR 300 CD] [16-44]', 'pressing'),
    ('2001 - Satanized (EP) [NPR 088 CD] [16-44]', 'other'),
    ('1999 - Channeling the Quintessence of Satan [NPR 062 CD] [16-44]', 'other'),
])
def test_library_classify(existing, cls):
    lib = _lib()
    target = '2001 - Satanized [NPR 088 CD] [16-44]'
    assert lib.classify(lib.album_ident(target), lib.album_ident(existing), target, existing)[0] == cls


def test_library_quality_direction():
    lib = _lib()
    t, e = '2001 - X [CD] [16-44]', '2001 - X [CD] [320K]'
    assert lib.classify(lib.album_ident(t), lib.album_ident(e), t, e) == ('quality', 'yours is an upgrade')


def test_library_finds_the_band_in_letter_folders_and_flat_roots(tmp_path):
    lib = _lib()
    sorted_, staging = tmp_path / 'Sorted', tmp_path / 'Metal'
    for p in ('A/Abigor (AT)/1994 - Verwustung [NPR 005 CD] [16-44]', 'A/Absu (US)/1993 - Barathrum [CD] [16-44]',
              'M/Motörhead (GB)/1980 - Ace of Spades [CD] [16-44]', 'B/x', 'C/x', 'D/x'):
        (sorted_ / p).mkdir(parents=True)
    (staging / 'Abigor (AT)' / '2001 - Satanized [NPR 088 CD] [16-44]').mkdir(parents=True)
    (staging / 'Abigor (AT)' / '2001 - Satanized [NPR 088 CD] [16-44]' / '01.flac').write_bytes(b'x')
    got = lib.scan([('Staging', str(staging)), ('Sorted', str(sorted_))], 'Abigor',
                   str(staging / 'Abigor (AT)' / '2001 - Satanized [NPR 088 CD] [16-44]'))
    assert [(r['where'], r['class'], r['audio']) for r in got['rows']] == [('Staging', 'collision', 1),
                                                                           ('Sorted', 'other', 0)]
    assert lib.find_artist_dirs(str(sorted_), 'Motorhead')[0].endswith('Motörhead (GB)')


def test_library_same_name_bands_prefer_the_target_country(tmp_path):
    lib = _lib()
    for n in ('Sacrifice (CA)', 'Sacrifice (JP)', 'Sacrifice (DE) (2006)'):
        (tmp_path / n).mkdir()
    found = lib.find_artist_dirs(str(tmp_path), 'Sacrifice', 'Sacrifice (JP)')
    assert [Path(p).name for p in found] == ['Sacrifice (JP)']
    assert len(lib.find_artist_dirs(str(tmp_path), 'Sacrifice', 'Sacrifice (XU)')) == 3
    assert lib.split_artist_folder('Sacrifice (DE) (2006)') == ('Sacrifice', 'DE')


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows path length limits')
def test_extras_in_a_folder_past_the_windows_path_limit(tmp_path):
    # Audit part 2 L5: files under a path of 260+ characters were silently skipped.
    ex = _mod()
    fs = sys.modules['picard.plugins.metallib_folder_xtest.folder_scan']
    deep = str(tmp_path)
    while len(deep) < 270:
        deep = os.path.join(deep, 'a very long folder name of a scene release')
    os.makedirs(fs._long(deep))
    with open(fs._long(os.path.join(deep, 'folder.jpg')), 'wb') as fh:
        fh.write(b'x' * 10)
    got = ex.list_extras([deep], [])
    assert [(e['name'], e['size']) for e in got] == [('folder.jpg', 10)]
    assert got[0]['path'] == os.path.join(deep, 'folder.jpg')          # no long-path prefix in results
    assert fs.short(fs._long(deep)) == deep

# MetalLib folder contents -- what else sits in an album folder, and what doesn't belong there.
#
# The user's finished library keeps ONLY audio and images (measured on ~60k files of the sorted
# library: flac / mp3 / jpg / png / tif / bmp -- nothing else), covers named
# "Artist - Album - 00 - Front.ext". So:
#   * anything that is neither audio nor an image (.nfo .sfv .log .cue .m3u .txt .url Thumbs.db ...)
#     and any 0-byte file is JUNK;
#   * audio the album does not use (not loaded, or missing from a .sfv that describes the folder)
#     is only CHECK -- it may be a real track, never proposed for removal automatically;
#   * a single image not yet named "... - 00 - Front.ext" is proposed for RENAME.
# Removal = a move into ".metallib_trash" at the root of the same drive/share (network shares have
# no Recycle Bin; a same-volume move is instant), logged so MetalLib can UNDO it, renames included.
# Nothing here ever deletes.
#
# No Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import re
import shutil
import time
import zlib


AUDIO_EXTS = {'.mp3', '.flac', '.wav', '.m4a', '.ogg', '.oga', '.opus', '.ape', '.wma', '.aac', '.alac',
              '.dsf', '.dff', '.wv', '.tta', '.mka', '.aiff', '.aif', '.mpc', '.mp2'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff'}
TRASH_DIR = '.metallib_trash'
SFV_TRUST = 0.6            # a .sfv must list >= 60 % of the audio to be believed (a stale one would flag everything)

JUNK, CHECK, RENAME, KEEP = 'junk', 'check', 'rename', 'keep'


# Windows refuses paths from 248 characters (folders; 260 for files) unless they carry the \\?\
# prefix (audit part 2 L5: the old threshold of 250 missed folders of 248-249 characters).
LONG_FROM = 240


def _long(path):
    if os.name == 'nt' and path and len(path) >= LONG_FROM and not path.startswith('\\\\?\\'):
        path = os.path.abspath(path)            # the prefix turns off Windows' own path clean-up
        return '\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path
    return path


def short(path):
    """A path without the long-path prefix (what the rest of MetalLib and Picard compare with)."""
    if path.startswith('\\\\?\\UNC\\'):
        return '\\\\' + path[8:]
    if path.startswith('\\\\?\\'):
        return path[4:]
    return path


def _sfv_names(folder, names):
    listed = set()
    for n in names:
        if n.lower().endswith('.sfv'):
            try:
                with open(_long(os.path.join(folder, n)), 'r', encoding='latin-1') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith(';'):
                            listed.add(os.path.basename(line.rsplit(' ', 1)[0].replace('\\', '/')).lower())
            except OSError:
                continue
    return listed


def _safe_name(text):
    return re.sub(r'[\\/:*?"<>|]+', '_', text or '').strip(' .')


# Already a cover name: "... - 00 - Front.jpg", multi-disc "... - 0-00 - Front.jpg".
_FRONT_RE = re.compile(r' - (?:\d+-)?0{2} - Front\.[A-Za-z]{3,4}$')
# "<prefix> - NN - Title.ext" / "<prefix> - 1-NN - Title.ext" -- the library's track file names.
_TRACK_NAME_RE = re.compile(r'^(.+?) - (?:\d+-)?\d{2,3} - .+\.[A-Za-z0-9]{2,4}$')


def is_front_name(name):
    return bool(_FRONT_RE.search(name))


def track_prefix(audio_paths):
    """The shared "Artist - Album [CATNO CD]" prefix of the album's track file names, or ''."""
    prefixes = {m.group(1) for m in (_TRACK_NAME_RE.match(os.path.basename(p)) for p in audio_paths) if m}
    return prefixes.pop() if len(prefixes) == 1 and audio_paths else ''


def front_name(artist, album, ext, prefix=''):
    """The library's cover name: "<track prefix> - 00 - Front.jpg", else "Artist - Album - 00 - Front.jpg"."""
    head = prefix or '%s - %s' % (_safe_name(artist), _safe_name(album))
    return '%s - 00 - Front%s' % (head, ext.lower())


def scan(folders, used_audio, artist='', album=''):
    """Every non-album file in `folders` (and one sub-folder level: disc / scans folders) with a
    verdict. `used_audio` = audio paths the album actually uses. -> [{path, name, size, verdict, reason,
    target}] sorted junk -> check -> rename -> keep."""
    used = {os.path.normcase(os.path.abspath(p)) for p in used_audio}
    entries, images = [], []
    for folder in sorted(set(folders)):
        try:
            top = sorted(os.listdir(_long(folder)))
        except OSError:
            continue
        paths = []
        for name in top:
            full = os.path.join(folder, name)
            if name == TRASH_DIR:
                continue
            if os.path.isdir(_long(full)):
                try:
                    paths += [os.path.join(full, n) for n in sorted(os.listdir(_long(full)))
                              if os.path.isfile(_long(os.path.join(full, n)))]
                except OSError:
                    pass
            else:
                paths.append(full)
        audio_here = [p for p in paths if os.path.splitext(p)[1].lower() in AUDIO_EXTS]
        sfv = _sfv_names(folder, top)
        trust_sfv = bool(sfv) and sum(os.path.basename(p).lower() in sfv for p in audio_here) >= \
            max(1, int(len(audio_here) * SFV_TRUST))
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            key = os.path.normcase(os.path.abspath(p))
            try:
                size = os.path.getsize(_long(p))
            except OSError:
                size = None                      # unknown is never called empty (fail closed)
            entry = {'path': p, 'name': os.path.relpath(p, folder), 'size': size, 'target': ''}
            if size == 0:
                entry.update(verdict=JUNK, reason='empty file (0 bytes)')
            elif ext in AUDIO_EXTS:
                if key in used:
                    continue                     # an album track: not listed at all
                if trust_sfv and os.path.basename(p).lower() not in sfv:
                    entry.update(verdict=CHECK, reason='audio not listed in the .sfv - may not belong')
                else:
                    entry.update(verdict=CHECK, reason='audio not used by this album in MetalLib')
            elif ext in IMAGE_EXTS:
                entry.update(verdict=KEEP, reason='image')
                images.append(entry)
            else:
                entry.update(verdict=JUNK, reason='%s file - the library keeps only audio and images'
                             % (ext or 'extension-less'))
            entries.append(entry)
    # A LONE image becomes the cover, under the library's name, unless it already has one.
    prefix = track_prefix(list(used_audio))
    if len(images) == 1 and ((artist and album) or prefix) and not is_front_name(os.path.basename(images[0]['path'])):
        img = images[0]
        want = front_name(artist, album, os.path.splitext(img['path'])[1], prefix)
        if os.path.basename(img['path']) != want:
            img.update(verdict=RENAME, reason='the only image: rename to the cover name',
                       target=os.path.join(os.path.dirname(img['path']), want))
    order = {JUNK: 0, CHECK: 1, RENAME: 2, KEEP: 3}
    return sorted(entries, key=lambda e: (order[e['verdict']], e['name'].lower()))


def summary(entries):
    """Short text for a column: "clean", or "3 junk, 1 check, 1 rename"."""
    counts = {}
    for e in entries:
        if e['verdict'] != KEEP:
            counts[e['verdict']] = counts.get(e['verdict'], 0) + 1
    if not counts:
        return 'clean'
    return ', '.join('%d %s' % (counts[v], v) for v in (JUNK, CHECK, RENAME) if v in counts)


# -- reversible actions -------------------------------------------------------------------------------

def trash_root(path):
    """Root of the drive or share holding `path`: "Y:\\" or "\\\\server\\share"."""
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    return drive + os.sep if drive and not drive.startswith('\\\\') else drive


class ActionLog:
    """Every move/rename, grouped in batches, so it can be undone (newest batch first)."""

    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def append(self, batch, src, dst, kind):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'batch': batch, 'src': src, 'dst': dst, 'kind': kind, 'undone': False}) + '\n')

    def last_batch(self):
        rows = [r for r in self._read() if not r.get('undone')]
        return rows[-1]['batch'] if rows else None

    def batches(self, folders=None):
        """[(batch, [rows not undone])], newest first -- only batches that touched `folders` (a file
        moved from or into one of them) when given. Any batch can be picked, so one that cannot be
        fully undone (a name taken again) never hides the older ones (audit part 2 M5)."""
        keys = [os.path.normcase(os.path.normpath(f)) for f in folders or ()]

        def inside(path):
            d = os.path.normcase(os.path.normpath(os.path.dirname(path or '')))
            return any(d == k or d.startswith(k + os.sep) for k in keys)
        by = {}
        for r in self._read():
            if not r.get('undone'):
                by.setdefault(r['batch'], []).append(r)
        out = [(b, rows) for b, rows in by.items()
               if not keys or any(inside(r['src']) or inside(r['dst']) for r in rows)]
        return sorted(out, key=lambda br: br[0], reverse=True)

    def undo(self, batch):
        """Move every file of `batch` back (never over an existing file). -> [(ok, message)]."""
        rows = self._read()
        results = []
        for r in reversed([r for r in rows if r['batch'] == batch and not r.get('undone')]):
            if os.path.exists(_long(r['src'])):
                results.append((False, '%s: the original name is taken again' % os.path.basename(r['src'])))
                continue
            try:
                os.makedirs(_long(os.path.dirname(r['src'])), exist_ok=True)
                shutil.move(_long(r['dst']), _long(r['src']))
                r['undone'] = True
                results.append((True, os.path.basename(r['src'])))
            except OSError as e:
                results.append((False, '%s: %s' % (os.path.basename(r['src']), e)))
        # atomically: a crash mid-write must not lose the log (it is the only record of what is in
        # .metallib_trash and where it came from)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')
        os.replace(tmp, self.path)
        return results


def new_batch():
    return time.strftime('%Y%m%d-%H%M%S') + '-%04x' % (zlib.crc32(str(time.time()).encode()) & 0xffff)


def move_to_trash(path, log, batch):
    """Move `path` into <drive/share root>/.metallib_trash/<batch>/<path below the root>."""
    root = trash_root(path)
    rel = os.path.relpath(os.path.abspath(path), root) if root else os.path.basename(path)
    dst = os.path.join(root, TRASH_DIR, batch, rel)
    os.makedirs(_long(os.path.dirname(dst)), exist_ok=True)
    shutil.move(_long(path), _long(dst))
    log.append(batch, path, dst, 'trash')
    return dst


def rename(path, target, log, batch):
    if os.path.exists(_long(target)):
        raise FileExistsError('%s already exists' % os.path.basename(target))
    os.rename(_long(path), _long(target))
    log.append(batch, path, target, 'rename')
    return target


def move_file(path, target, log, batch):
    """Move `path` to `target`, also to another drive (album folders move from the download drive to
    the library's); never over an existing file. Logged, so it can be undone."""
    if os.path.exists(_long(target)):
        raise FileExistsError('%s already exists' % os.path.basename(target))
    os.makedirs(_long(os.path.dirname(target)), exist_ok=True)
    shutil.move(_long(path), _long(target))
    log.append(batch, path, target, 'move')
    return target

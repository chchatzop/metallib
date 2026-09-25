# MetalLib extra files -- everything in an album's folder(s) that is not the album's audio: what it
# is, whether it moves with the album, and the name it gets there (user, 2026-09-25).
#
# The user's library (6,814 albums surveyed): only images are kept, flattened into the album folder
# (no subfolders), named with the TRACKS' prefix and a zero track number:
#     single disc  "Abbath - Dread Reaver (Lim. Ed.) - 00 - Booklet 01.jpg"
#     multi-disc   "...and Oceans - The Symmetry of I ... - 0-00 - Back.jpg"
# Usual names: Front, Back, CD, CD1, CD Matrix, Inlay, Booklet 01..16, Booklet A/B, Digi Cover,
# Digi Inside, OBI, Jewel Case, Sticker, Jap. Booklet 01, Box A, DVD, Photo ...
# Defaults: images move (a lone image becomes Front); proofs ("proof" / "prf" in the name --
# user: not the group's tag, covers carry it too), text and junk files do not -- they go to the
# trash. The user ticks and renames in the Extra files panel.
#
# No Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import re

from .folder_scan import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    _long,
    short,
)


TEXT_EXTS = {'.nfo', '.txt', '.log', '.cue', '.m3u', '.m3u8', '.sfv', '.md5', '.ffp', '.accurip', '.url',
             '.ini', '.diz', '.json', '.xml', '.csv'}
IMAGE, TEXT, OTHER = 'image', 'text', 'other'


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXTS:
        return IMAGE
    if ext in TEXT_EXTS:
        return TEXT
    return OTHER


def is_proof(name):
    return bool(re.search(r'proof|prf', name, re.I))


def list_extras(folders, album_audio):
    """Every non-audio file in `folders` and their subfolders (also audio that is not the album's):
    [{'path', 'rel', 'folder', 'name', 'kind', 'size'}], folder by folder, sorted by name."""
    album_audio = {os.path.normcase(os.path.abspath(p)) for p in album_audio}
    out, seen = [], set()
    for folder in folders:
        for dirpath, dirs, files in os.walk(_long(folder)):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.metallib_trash'))
            dirpath = short(dirpath)
            for name in sorted(files, key=str.lower):
                path = os.path.join(dirpath, name)
                key = os.path.normcase(os.path.abspath(path))
                if key in seen or key in album_audio:
                    continue
                seen.add(key)
                if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                    kind = OTHER                  # audio the album does not use: listed, never moved by default
                else:
                    kind = kind_of(name)
                try:
                    size = os.path.getsize(_long(path))
                except OSError:
                    size = 0
                out.append({'path': path, 'rel': os.path.relpath(path, folder), 'folder': folder, 'name': name,
                            'kind': kind, 'size': size})
    return out


# -- names ------------------------------------------------------------------------------------------

_LIBRARY_RE = re.compile(r' - (?:\d+-)?0{2} - (.+)$')


def _number(text):
    """The last number in a name, two digits: "scan_3" -> "03"."""
    nums = re.findall(r'\d+', text)
    return '%02d' % int(nums[-1]) if nums else ''


def guess_stem(name):
    """What an image (or other extra) is called in the library, from its current name; '' when the
    name says nothing recognisable. Numbering already in the name is kept ("booklet_03" -> "Booklet 03")."""
    base = os.path.splitext(name)[0]
    m = _LIBRARY_RE.search(base)
    if m:
        return m.group(1).strip()             # already named the library's way
    low = re.sub(r'[_.\-]+', ' ', base.lower())
    words = set(low.split())
    num = _number(re.sub(r'(?:19|20)\d\d', '', low))      # ignore years
    jap = 'Jap. ' if re.search(r'\bjap(an(ese)?)?\b|\bjpn\b', low) else ''
    cdn = re.search(r'\b(?:cd|disc|disk)\s*(\d)\b', low)
    cd = 'CD%s' % cdn.group(1) if cdn else 'CD'
    if 'proof' in low or 'prf' in words:
        return 'Proof'
    if 'matrix' in low:
        return '%s Matrix' % cd
    if re.search(r'\bobi\b', low):
        return 'OBI Back' if 'back' in low else 'OBI'
    if 'sticker' in low:
        return 'Sticker'
    if 'inlay' in low or re.search(r'\btray\b', low):
        return 'Inlay'
    if 'jewel' in low:
        return 'Jewel Case'
    if re.search(r'\bdigi', low):
        if re.search(r'\b(inside|inner|in|interior)\b', low):
            return 'Digi Inside'
        return 'Digi Cover'
    if re.search(r'booklet|\bbook\b|\bpage|\bscan|\binsert|leaflet', low):
        letter = re.search(r'\b([a-d])\b', low)
        if num:
            return '%sBooklet %s' % (jap, num)
        return '%sBooklet %s' % (jap, letter.group(1).upper()) if letter else '%sBooklet' % jap
    if re.search(r'\b(back|rear|contra|backcover)\b', low):
        return 'Back'
    if re.search(r'\b(front|cover|folder|portada|frontcover|albumart)', low):
        return 'Front'
    if re.search(r'\b(cd|disc|disk|cd\d)\b', low):
        return cd
    if re.search(r'\bdvd\b', low):
        return 'DVD'
    if 'poster' in low:
        return 'Poster'
    if 'photo' in low:
        return 'Photo'
    return ''


def clean_stem(name):
    """An unrecognised name kept readable: no leading track numbers, spaces instead of _ ."""
    base = os.path.splitext(name)[0]
    base = re.sub(r'^\s*\d{1,3}\s*[-_. ]+\s*', '', base)
    base = re.sub(r'[_]+', ' ', base).strip(' -.')
    return base[:1].upper() + base[1:] if base else 'Image'


def plan(entries, user=None):
    """Tick and stem for every entry: the user's choice (user[path] = {'tick', 'stem'}) where given,
    else the defaults. Images move; a lone image is Front; proofs, text and other files stay out.
    Unnumbered booklets are numbered in name order; equal stems get " 2", " 3"."""
    user = user or {}
    images = [e for e in entries if e['kind'] == IMAGE]
    lone = len(images) == 1
    for e in entries:
        stem = guess_stem(e['name']) if e['kind'] == IMAGE else ''
        if e['kind'] == IMAGE and lone and stem in ('', 'Booklet', 'Digi Cover'):
            stem = 'Front'                    # the only image: the cover, unless its name says otherwise
        if e['kind'] == IMAGE and not stem:
            stem = clean_stem(e['name'])
        e['guess'] = stem
        e['tick'] = e['kind'] == IMAGE and not is_proof(e['name'])
    # "Booklet" without a number, in name order -> Booklet 01, 02, ...
    plain = [e for e in images if e['guess'] in ('Booklet', 'Jap. Booklet')]
    if len(plain) > 1:
        for i, e in enumerate(plain, 1):
            e['guess'] = '%s %02d' % (e['guess'], i)
    for e in entries:
        u = user.get(e['path']) or {}
        e['stem'] = u.get('stem') or e['guess']
        if 'tick' in u:
            e['tick'] = u['tick']
    # equal names among the moving files: "Front", "Front 2" ...
    taken = {}
    for e in entries:
        if not e['tick'] or not e['stem']:
            continue
        key = (e['stem'].lower(), os.path.splitext(e['name'])[1].lower())
        taken[key] = taken.get(key, 0) + 1
        if taken[key] > 1:
            e['stem'] = '%s %d' % (e['stem'], taken[key])
    return entries


def prefix_of(track_name):
    """From a track's (new) file name: (prefix, multi_disc). "A - B - 01 - T.flac" -> ("A - B", False),
    "A - B - 2-05 - T.flac" -> ("A - B", True); (None, False) when it has no " - NN - " part."""
    m = re.match(r'^(.*?) - (\d+-)?\d{2,3} - ', os.path.basename(track_name))
    return (m.group(1), bool(m.group(2))) if m else (None, False)


def target_name(prefix, multi, stem, ext):
    return '%s - %s - %s%s' % (prefix, '0-00' if multi else '00', stem, ext.lower())


# -- carrying it out (after the album's audio was saved) ----------------------------------------------

def foreign_audio(entries):
    """Audio in the folder(s) that is not the album's: another album shares the folder."""
    return [e for e in entries if os.path.splitext(e['name'])[1].lower() in AUDIO_EXTS]


def _free(target):
    base, ext = os.path.splitext(target)
    n = 2
    while os.path.exists(_long(target)):
        target = '%s (%d)%s' % (base, n, ext)
        n += 1
    return target


def execute(entries, prefix, multi, dest, log, batch, move_file, move_to_trash):
    """Ticked files -> `dest` under the library's names; unticked -> the trash (never audio: that
    stays where it is). Then empty source folders are removed. -> {'moved', 'trashed', 'errors'}."""
    done = {'moved': [], 'trashed': [], 'errors': []}
    if not prefix:
        return done
    for e in entries:
        src = e['path']
        if not os.path.exists(_long(src)):
            continue
        try:
            if e['tick']:
                target = os.path.join(dest, target_name(prefix, multi, e['stem'], os.path.splitext(e['name'])[1]))
                if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(src)):
                    continue                  # already there under that name
                done['moved'].append((src, move_file(src, _free(target), log, batch)))
            elif os.path.splitext(e['name'])[1].lower() not in AUDIO_EXTS:
                done['trashed'].append((src, move_to_trash(src, log, batch)))
        except OSError as err:
            done['errors'].append('%s: %s' % (e['name'], err))
    dest_key = os.path.normcase(os.path.abspath(dest))
    for folder in {e['folder'] for e in entries}:
        remove_empty_dirs(folder, keep=dest_key)
    return done


def remove_empty_dirs(folder, keep=None):
    """Remove `folder` and its subfolders when they are empty (never one holding a file, never `keep`)."""
    for dirpath, dirs, files in os.walk(_long(folder), topdown=False):
        key = os.path.normcase(os.path.abspath(short(dirpath)))
        if key == keep:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass

# MetalLib -- "Already in your library": the band's albums that are already filed, next to the one
# being tagged. Pure logic (no Qt, no Picard): find the band's folder(s) under the library roots and
# compare each album folder with the folder the new album will get.
#
# Folder names follow the library layout (Z:\1 Metal\1 Sorted, 6,577 of 6,577 albums):
#     YYYY - Title (EP) (RE 2004) (Digipak) [NPR 154 CD] [16-44]
# so the name alone says album, pressing and quality -- no tags are read (as in full_program's
# /api/retag/collisions, which this ports; its catalog lookup was broken, fixed here).
#
# Classes (most urgent first):
#   COLLISION  the exact folder the new album would move into -- it would MERGE into it
#   REDUNDANT  same album, pressing and quality under another folder name
#   QUALITY    same album and pressing, other quality (upgrade / downgrade)
#   PRESSING   same album, other pressing (catalog, media, reissue/remaster year, edition, digipak)
#   OTHER      another album of the band
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import re
import unicodedata


COLLISION, REDUNDANT, QUALITY, PRESSING, OTHER, THIS = 'collision', 'redundant', 'quality', 'pressing', 'other', 'this'
ORDER = (COLLISION, REDUNDANT, QUALITY, PRESSING, OTHER, THIS)

AUDIO_EXTS = frozenset(('.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.wv', '.ape', '.aiff', '.aif', '.dsf',
                        '.dff', '.wma', '.mpc', '.alac', '.aac', '.tta'))
IMAGE_EXTS = frozenset(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'))

# -- artist ----------------------------------------------------------------------------------------------

_TRANSLIT = str.maketrans({'ø': 'o', 'æ': 'ae', 'œ': 'oe', 'ß': 'ss', 'ð': 'd', 'þ': 'th', 'ł': 'l', 'đ': 'd',
                           'ı': 'i'})


def artist_key(name):
    """Same artist whatever the spelling: case, diacritics, a leading "The", &/and, punctuation and
    spaces fold away (port of full_program tag_reader.artist_key)."""
    s = (name or '').strip().lower()
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).translate(_TRANSLIT)
    stripped = re.sub(r'^the\s+', '', s)
    if stripped.strip() and stripped.strip() != 'the':
        s = stripped
    if re.search(r'[a-z0-9]', s):
        s = s.replace('&', ' and ')
    key = re.sub(r'[^a-z0-9]+', '', s)
    if key:
        return key
    return 'u:' + re.sub(r'[\W_]+', '', s)              # non-Latin names keep their own letters


# "Band (DE)", "Band (DE) (2006)" (same-name bands get the formation year), "Band (XW)"
_ARTIST_TAIL_RE = re.compile(r'(?:\s*\((?:[A-Za-z]{2}|\d{4})\))+\s*$')
_CC_RE = re.compile(r'\(([A-Za-z]{2})\)')


def split_artist_folder(name):
    """-> (artist name, country code or '')."""
    tail = _ARTIST_TAIL_RE.search(name)
    if not tail:
        return name.strip(), ''
    cc = _CC_RE.search(tail.group(0))
    return name[:tail.start()].strip(), cc.group(1).upper() if cc else ''


def _is_bucketed(names):
    """A root split into letter folders (A, B, ..., #) rather than artist folders."""
    names = [n for n in names if not n.startswith('.')]
    return bool(names) and sum(1 for n in names if len(n) == 1 or n == '#') >= 0.6 * len(names)


def _bucket_of(artist):
    c = unicodedata.normalize('NFKD', (artist or '?')[0]).upper()
    return c if 'A' <= c <= 'Z' else '#'


def find_artist_dirs(root, artist, target_dir_name='', listdir=None):
    """Folders under `root` that hold `artist`. Same-name bands: the target's exact folder name
    wins, then the same country code; if neither decides, all of them."""
    listdir = listdir or _subdirs
    key = artist_key(artist)
    if not key:
        return []
    top = listdir(root)
    if _is_bucketed(top):
        wanted = [b for b in (_bucket_of(artist), '#') if b in top] or top
        places = [os.path.join(root, b) for b in dict.fromkeys(wanted)]
    else:
        places = [root]
    found = []
    for place in places:
        names = top if place == root else listdir(place)
        found += [os.path.join(place, n) for n in names if artist_key(split_artist_folder(n)[0]) == key]
    if len(found) > 1 and target_dir_name:
        exact = [p for p in found if os.path.basename(p).casefold() == target_dir_name.casefold()]
        if exact:
            return exact
        cc = split_artist_folder(target_dir_name)[1]
        same = [p for p in found if cc and split_artist_folder(os.path.basename(p))[1] == cc]
        if same:
            return same
    return found


def _subdirs(path):
    try:
        return sorted(e.name for e in os.scandir(path) if e.is_dir())
    except OSError:
        return []


# -- album folders -----------------------------------------------------------------------------------------

_YEAR_RE = re.compile(r'^\s*(\d{4})\s*-\s*')
_BRACKET_RE = re.compile(r'\[([^\[\]]*)\]')
_PAREN_RE = re.compile(r'\(([^()]*)\)')
_QUALITY_RE = re.compile(r'^(?:\d{1,2}-\d{2,3}(?:\.\d)?|\d{2,3}K?|V\d|VBR|MIXED|LOSSLESS)$', re.IGNORECASE)
# parentheses that tell PRESSINGS apart (Promo/Bootleg: the release status); every other one
# (EP, Live, Demo, a subtitle) names the album
_PRESSING_PAREN_RE = re.compile(r'^(?:RE|RM)\s+\d{4}$|^Digipak$|^Promo$|^Bootleg$|^(?:Jap|Lim|Del|US|Exp|Exp\. Del)\. Ed\.$',
                                re.IGNORECASE)


def _norm(s):
    s = unicodedata.normalize('NFKD', s or '').lower()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[\W_]+', '', s)


def album_ident(name):
    """-> {'year', 'album', 'pressing', 'quality', 'title'} from a library album folder name."""
    year = ''
    m = _YEAR_RE.match(name)
    rest = name
    if m:
        year, rest = m.group(1), name[m.end():]
    brackets = _BRACKET_RE.findall(rest)
    quality = ''
    if brackets and _QUALITY_RE.match(brackets[-1].strip()):
        quality = brackets.pop().strip().upper()
    bracket = brackets[-1].strip() if brackets else ''
    title = _BRACKET_RE.sub(' ', rest)
    marks = []

    def paren(m):
        inner = m.group(1).strip()
        if _PRESSING_PAREN_RE.match(inner):
            marks.append(_norm(inner))
            return ' '
        return m.group(0)
    title = _PAREN_RE.sub(paren, title).strip()
    pressing = '|'.join([_norm(bracket)] + sorted(marks))
    return {'year': year, 'title': title, 'album': _norm(title), 'pressing': pressing, 'quality': quality}


def quality_rank(q):
    q = (q or '').upper()
    m = re.match(r'^(\d{1,2})-(\d{2,3})', q)
    if m:
        return 100000 + int(m.group(1)) * 1000 + int(m.group(2))
    if q == 'LOSSLESS':
        return 100000
    m = re.match(r'^(\d{2,3})K?$', q)
    if m:
        return 1000 + int(m.group(1))
    m = re.match(r'^V(\d)$', q)
    if m:
        return 1300 - int(m.group(1)) * 10          # V0 ~ 300 kbps, V2 ~ 190-250
    return 0


def classify(target, existing, target_name, existing_name):
    """-> (class, note)."""
    if existing['album'] != target['album']:
        return OTHER, 'another album'
    if existing['pressing'] != target['pressing']:
        return PRESSING, 'same album, another pressing'
    if existing['quality'] != target['quality']:
        a, b = quality_rank(target['quality']), quality_rank(existing['quality'])
        if a and b and a != b:
            return QUALITY, ('yours is an upgrade' if a > b else 'yours is a downgrade')
        return QUALITY, 'another quality'
    if ' '.join(existing_name.split()).casefold() == ' '.join(target_name.split()).casefold():
        return COLLISION, 'the exact folder yours moves into — saving would merge into it'
    return REDUNDANT, 'same album, pressing and quality under another name'


def count_files(folder):
    """(audio, images) in the album folder and one level of disc folders; None, None if unreadable."""
    audio = images = 0
    try:
        stack = [(folder, 0)]
        while stack:
            path, depth = stack.pop()
            for e in os.scandir(path):
                if e.is_dir():
                    if depth == 0:
                        stack.append((e.path, 1))
                    continue
                ext = os.path.splitext(e.name)[1].lower()
                audio += ext in AUDIO_EXTS
                images += ext in IMAGE_EXTS
    except OSError:
        return None, None
    return audio, images


def scan(roots, artist, target_folder, source_folders=(), counts=True):
    """Thread-safe (filesystem only). roots: [(label, path)]. target_folder: the full path the album
    will move into. -> {'artist_dirs': [(label, path)], 'rows': [row]} sorted most urgent first."""
    target_name = os.path.basename(target_folder or '')
    target_artist_dir = os.path.basename(os.path.dirname(target_folder or ''))
    target = album_ident(target_name)
    own = {os.path.normcase(os.path.normpath(p)) for p in source_folders}
    rows, dirs = [], []
    for label, root in roots:
        if not root or not os.path.isdir(root):
            continue
        for adir in find_artist_dirs(root, artist, target_artist_dir):
            dirs.append((label, adir))
            for name in _subdirs(adir):
                path = os.path.join(adir, name)
                ident = album_ident(name)
                if os.path.normcase(os.path.normpath(path)) in own:
                    cls, note = THIS, 'the album you are tagging'
                else:
                    cls, note = classify(target, ident, target_name, name)
                audio, images = count_files(path) if counts else (None, None)
                rows.append({'where': label, 'artist_dir': os.path.basename(adir), 'name': name, 'path': path,
                             'class': cls, 'note': note, 'year': ident['year'], 'audio': audio, 'images': images})
    rows.sort(key=lambda r: (ORDER.index(r['class']), r['year'], r['name'].casefold()))
    return {'artist_dirs': dirs, 'rows': rows}


def default_roots(staging):
    """[(label, path)]: the staging folder (Picard's "move files to", e.g. Z:\\Metal) and every
    subfolder of its archive sibling (Z:\\1 Metal: 1 Sorted, 3 Greek) -- labels without the number."""
    roots = []
    if not staging:
        return roots
    staging = os.path.normpath(staging)
    roots.append(('Staging', staging))
    parent, base = os.path.split(staging)
    for sib in _subdirs(parent) if parent else []:
        path = os.path.join(parent, sib)
        if sib != base and sib.lower().endswith(base.lower()) and os.path.normcase(path) != os.path.normcase(staging):
            for sub in _subdirs(path):
                roots.append((re.sub(r'^\d+\s*', '', sub) or sub, os.path.join(path, sub)))
    return roots

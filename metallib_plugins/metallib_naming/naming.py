# MetalLib naming -- pure helpers behind the naming-script functions (no Picard imports).
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re
import unicodedata


# Letters NFKD does not decompose.
_TRANSLIT = str.maketrans({'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ß': 'ss',
                           'đ': 'd', 'Đ': 'D', 'ł': 'l', 'Ł': 'L', 'þ': 'th', 'Þ': 'TH', 'ð': 'd', 'Ð': 'D'})


def first_letter(text):
    """Letter folder: "Abbath" -> "A", "Ångström" -> "A", "1349" / "...and Oceans" -> "#".
    A leading "The" is kept, as in the library ("The Andronovo" is under T)."""
    s = unicodedata.normalize('NFKD', (text or '').translate(_TRANSLIT))
    s = ''.join(c for c in s if not unicodedata.combining(c)).strip()
    c = s[:1].upper()
    return c if 'A' <= c <= 'Z' else '#'


def media_code(text):
    """The folder's source tag from a media value: "Digital Media" -> WEB, "CD" / "SHM-CD" / "2×CD"
    -> CD, '12" Vinyl' -> LP, "Cassette" -> Tape. A mixed set ("CD / DVD") takes its first medium."""
    first = re.split(r'\s*(?:/|\+|;)\s*', (text or '').strip())[0].lower()
    if not first:
        return ''
    if re.search(r'digital|\bweb\b|\bfile\b|download|stream', first):
        return 'WEB'
    if re.search(r'blu-?ray', first):
        return 'BD'
    if 'dvd' in first:
        return 'DVD'
    if re.search(r'cd\b|cd-r|\bcdr\b|sacd|hdcd', first):
        return 'CD'
    if re.search(r'vinyl|\blp\b|\d+"|\bep\b', first):
        return 'LP'
    if re.search(r'cassette|tape|\bmc\b', first):
        return 'Tape'
    return ''


# Media tokens in a folder name (a port of full_program's dupe_scanner._detect_media): a scene or
# uploader name says what the copy in hand was ripped from, e.g. "Band-Album-WEB-2023-GRP".
_MEDIA_TOKENS = (
    ('WEB', re.compile(r'^(?:WEB|WEBDL|WEBRIP)$', re.I)),
    ('CD', re.compile(r'^(?:\d*CD|CD\d*|CDA|CDM|CDS|CDR|CDEP|MCD|CDDA|CDRIP|SACD|SCD)$', re.I)),
    ('LP', re.compile(r'^(?:VINYL|\d*LP|VLS|\d{1,2}INCH)$', re.I)),
    ('Tape', re.compile(r'^(?:TAPE|CASSETTE|CASS)$', re.I)),
)
_TOKEN_SPLIT = re.compile(r'[\s._\-()\[\]]+')


def folder_media(path):
    """WEB / CD / LP / Tape named in the file's folder name, '' when none or several."""
    name = re.split(r'[\\/]', (path or '').rstrip('\\/'))[-1]
    found = set()
    if re.search(r'[(\[]\s*digital\s*[)\]]', name, re.I):     # "[Digital]"; a bare word may be a title
        found.add('WEB')
    for tok in _TOKEN_SPLIT.split(name):
        for code, rx in _MEDIA_TOKENS:
            if tok and rx.match(tok):
                found.add(code)
                break
    return next(iter(found)) if len(found) == 1 else ''


def with_media(catalog, code):
    """Catalog bracket as in the library: "SOM 650B" + CD -> "SOM 650B CD"; a catalog that already
    ends with the media word stays ("TOR 105 LP"). Only the first of several catalog numbers."""
    catalog = re.split(r'\s*;\s*', (catalog or '').strip())[0]
    if not catalog or catalog.lower() in ('none', '[none]', 'n/a'):
        return code or ''
    if not code or catalog.split()[-1].upper() == code.upper():
        return catalog
    # A media word glued to the number is split off, as the library mostly has it:
    # "FO1282CD" -> "FO1282 CD", "KAR134LP" -> "KAR134 LP" (15 of 20 such albums sampled).
    glued = re.match(r'^(.*\d)-?%s$' % re.escape(code), catalog, re.I)
    if glued:
        return '%s %s' % (glued.group(1), code)
    return '%s %s' % (catalog, code)


def album_media(values):
    """Distinct media of an album's files, in order: ["CD", "CD", "DVD"] -> "CD / DVD"."""
    return ' / '.join(dict.fromkeys(v for v in values if v))

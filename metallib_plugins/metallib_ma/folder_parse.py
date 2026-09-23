# MetalLib -- parse artist / album / year / catalog / label / country out of a folder name.
#
# PORTED VERBATIM from the user's Tag & Rename tool (full_program/tag_reader.py, lines 23-491 and
# 645-1076: scene names, space-padded patterns, backtick uploader releases, [Label, CatNo, Country]
# tags, scene "(CATNO)" parens, format-tag stripping), with every calibration comment kept. The only
# change: _split_artist_album no longer consults the tool's offline Metal Archives index (MetalLib
# has none), so an ambiguous 3+ part scene name uses the parser's own default split.
#
# No Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re
from pathlib import Path

# ── Scene release detector ────────────────────────────────────────────────────
# Scene folders: Artist_Name-Album_Name-YEAR-GROUP or Artist.Name-Album.Name-YEAR-GROUP
_SCENE_RE = re.compile(
    r'^[A-Za-z0-9_.]+[-][A-Za-z0-9_.]+[-](\d{4})[-](?:[A-Za-z0-9]{2,12}|[A-Za-z0-9]{2,10}_[Ii][Nn][Tt])$'
)
# Looser scene check: year flanked by dashes, ends with a group tag.
# Internal releases end with _int/_INT/_Int suffix (e.g. MCA_int, NER_int).
_SCENE_LOOSE_RE = re.compile(
    # Tightened vs the naive '[-_]YEAR[-_]GROUP' to prevent false positives on
    # genre folders like 'Black Metal-2000-MIXED'. Requires a space-free token
    # (like an album name) immediately before the year separator.
    # B-M33/D5: despite the \s* below, space-padded separators ('Artist - Album
    # - 2024 - GROUP') NEVER reach this regex — both is_scene_release branches
    # require a space-free name. Space-padded scene renames are handled by the
    # dedicated 4-segment entry in _FOLDER_PATTERNS instead.
    r'(?:^|[-_.\s])([A-Za-z0-9][A-Za-z0-9_.]*)\s*[-_]\s*(\d{4})\s*[-_]\s*'
    r'(?:[A-Za-z0-9]{2,12}|[A-Za-z0-9]{2,10}_[Ii][Nn][Tt])$'
)

# B-H6: hard media/codec/source segments that PROVE a dash-only name is a scene release
# (an ordinary '<word>-<year>-<word>' album name never carries one as its own segment).
_SCENE_MEDIA_SEG_RE = re.compile(
    r'^(?:CD|CDR|CDM|CDS|CDEP|MCD|CDDA|\d?CD|FLAC|MP3|AAC|M4A|OGG|APE|WAV|ALAC|'
    r'WEB|WEBRIP|WEBFLAC|VINYL|LP|MLP|VLS|TAPE|CASSETTE|SACD|DVD|DVDA|\d+BIT|'
    r'RETAIL|PROPER|READNFO|REPACK|RERIP|320|256|192|128)$', re.IGNORECASE)


def is_scene_release(folder_name: str) -> bool:
    """Heuristic: does this folder name look like a scene release?

    _SCENE_RE is strict: requires underscores/dots as separators, no spaces.
    _SCENE_LOOSE_RE is trusted when the name has NO spaces AND either contains a
    scene-style separator ('_' or '.') OR (B-H6) one of its dash-segments is a hard
    media/codec/source token ('1349-Demonoir-CD-FLAC-2010-GRM' — underscore-less
    scene names previously fell through to generic parsing, which stored the release
    GROUP as the album at high confidence for ~17.9k real folders). Plain
    '<word>-<year>-<word>' album folders ('Live-2019-Tour', 'Album-2020-Remaster')
    carry no such segment and still fall through to normal parsing + tag fallback.
    """
    if ' ' not in folder_name and _SCENE_RE.match(folder_name):
        return True
    if ' ' not in folder_name and _SCENE_LOOSE_RE.search(folder_name):
        if '_' in folder_name or '.' in folder_name:
            return True
        if any(_SCENE_MEDIA_SEG_RE.match(seg) for seg in folder_name.split('-')):
            return True   # B-H6: dash-only scene name proven by a media/codec segment
    return False


# ── Folder name parser ────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')

# Patterns tried in order — first match wins
# Each entry: (regex, group_map)  group_map = {field: group_number}
# L92: edition/type words (≤12 chars) the B-M33 4th segment re-injects into the album.
# Lockstep with dupe_scanner._DEDUP_KEEP (minus live/demo/ep/single/split/promo, which the
# B-M33 negative lookahead already excludes) so a re-injected word survives dedup_album_key.
_SCENE_TAIL_KEEP = {
    'compilation', 'comp', 'bootleg', 'reissue', 'remaster', 'remastered', 'deluxe',
    'anniversary', 'expanded', 'limited', 'acoustic', 'instrumental', 'unplugged',
    'karaoke', 'rehearsal', 'rerecorded', 'rerecord', 'recorded', 'recording',
    'redux', 'mono', 'stereo', 'soundtrack', 'ost',
}

_FOLDER_PATTERNS = [
    # Year - Album (no artist, common for inner-artist-folder releases)
    (re.compile(r'^((?:19|20)\d{2})\s*[-–\s]\s*(.+)$'),
     {'year': 1, 'album': 2}),

    # B-M33: Artist - Album - Year - GROUP  (space-padded scene rename; the
    # trailing release-group token is dropped). MUST precede the Artist - Year -
    # Album pattern, whose greedy backtracking otherwise swallows 'Artist -
    # Album' as the artist and stores the GROUP token as the album. The negative
    # lookahead refuses to drop a known release-TYPE word (Live/Demo/EP/Single/
    # Split/Promo) — those are real title candidates ('… - 1999 - Live'), so
    # such names fall through to the existing patterns instead.
    # L92: CAPTURE the 4th segment as `_tail` (was discarded). A real scene-GROUP tag is
    # dropped, but an EDITION/TYPE word (Remastered/Deluxe/Reissue/…) is re-injected into the
    # album in parse_folder_name — else editions collapsed onto the base studio album in dedup.
    (re.compile(r'^(.+?)\s+[-–]\s+(.+?)\s+[-–]\s+((?:19|20)\d{2})\s+[-–]\s+'
                r'(?!(?i:live|demo|ep|single|split|promo)$)([A-Za-z0-9_]{2,12})$'),
     {'artist': 1, 'album': 2, 'year': 3, '_tail': 4}),

    # Artist - Year - Album   (space-padded ' - ' preferred)
    (re.compile(r'^(.+?)\s+[-–]\s+((?:19|20)\d{2})\s*[-–]\s*(.+)$'),
     {'artist': 1, 'year': 2, 'album': 3}),

    # Artist - Album (Year)  or  Artist - Album [Year]   (space-padded)
    (re.compile(r'^(.+?)\s+[-–]\s+(.+?)\s*[\(\[]((?:19|20)\d{2})[\)\]]'),
     {'artist': 1, 'album': 2, 'year': 3}),

    # B-M32: Artist - (Year) Album  or  Artist - [Year] - Album. MUST precede
    # the bare 'Album (Year)' pattern below, which otherwise wins (nothing
    # precedes the bracket for the pattern above to capture as the album) and
    # stores the ARTIST NAME as the album — collapsing every such album of one
    # artist onto a single (artist_key, dedup_key) identity.
    (re.compile(r'^(.+?)\s+[-–]\s+[\(\[]((?:19|20)\d{2})[\)\]]\s*-?\s*(.+)$'),
     {'artist': 1, 'year': 2, 'album': 3}),

    # Album (Year)  or  Album [Year]
    (re.compile(r'^(.+?)\s*[\(\[]((?:19|20)\d{2})[\)\]]'),
     {'album': 1, 'year': 2}),

    # Artist - Album  (space-padded ' - ' — REQUIRES whitespace on both sides of
    # the dash so a hyphenated BAND name isn't split: "AC-DC - Back in Black",
    # "Blink-182 - Album", "Jay-Z - The Blueprint" → artist keeps its hyphen.)
    (re.compile(r'^(.+?)\s+[-–]\s+(.+)$'),
     {'artist': 1, 'album': 2}),

    # ── Bare-hyphen fallbacks (dash with NO surrounding spaces) — only reached
    # when none of the space-padded patterns matched. Here a hyphen glued to both
    # sides is genuinely ambiguous (band name vs separator); the first-hyphen
    # split is the historical best-effort. "Megadeth-Rust in Peace" → Megadeth /
    # Rust in Peace (correct); "AC-DC-Live" stays ambiguous as before.
    (re.compile(r'^(.+?)[-–]((?:19|20)\d{2})[-–](.+)$'),
     {'artist': 1, 'year': 2, 'album': 3}),
    (re.compile(r'^(.+?)[-–](.+)$'),
     {'artist': 1, 'album': 2}),
]

# Strip only known format/source/release tags — NOT generic words.
# NOTE: LIVE, DEMO, SPLIT, SINGLE are deliberately EXCLUDED — they are extremely
# common real metal album titles ('Live', 'Demo', 'Split'), and stripping them
# erased the actual album ('Anthrax - Live (1991)' -> 'Anthrax (1991)'). Genuine
# format/source tags below are unambiguous.
# Strips ONLY pure format/source/codec tokens — NOT release-TYPE/EDITION words. An
# unbracketed edition suffix (' - Remastered', ' Deluxe', ' Promo', ' Bootleg', ' Limited')
# must SURVIVE into the parsed album, else dedup_album_key folds the remaster/deluxe/promo
# onto the base studio album and offers it for deletion (the edition distinction is the
# whole point of keeping these). Bracketed editions are re-injected by _DEDUP_KEEP; the
# unbracketed ones simply must not be erased here. (audit R3 [7])
_SUFFIX_STRIP = re.compile(
    r'[\s._-]+(WEB|WEB-DL|WEBDL|CDRip|CDR|CD|VINYL|SACD|'
    r'FLAC|MP3|APE|WAV|OGG|AAC|ALAC)(?=\s*[-_\[\(]|$)',
    re.IGNORECASE
)

# Trailing bracketed format/source/quality tags that pollute album titles, e.g.
# "King of Gods [WEB] [16-44]" or "Adderall [web]". Scope is deliberately
# limited to PURE format/source/quality tokens — codecs, media, bitrates,
# bit-depth/sample-rate, rip source. Release-TYPE words (EP, Demo, Single,
# Split, Live, Promo, Reissue, Remaster, Deluxe, Limited) are intentionally
# EXCLUDED: they distinguish editions and removing them would silently merge a
# live/demo/EP with its studio counterpart in dedup. A tag is only stripped
# when it sits inside brackets — a bare word is left alone (a real title may BE
# that word, e.g. an album literally called "Live" or "Web").
_FMT_TOKEN = (
    r'(?:'
    r'WEB(?:[-.]?DL)?|WEBDL|'
    r'\d?CDR?|CDRIP|VINYL|\d?LP|SACD|TAPE|CASSETTE|'
    r'FLAC|MP3|APE|WV|WAV|OGG|OPUS|AAC|ALAC|M4A|DSF|DSD|'
    r'320|256|224|192|160|128|V0|V2|CBR|VBR|EAC|'
    r'\d{2}[-/]\d{2,3}(?:\.\d)?|'          # bit-sample, e.g. 16-44 / 24-96 / 16-44.1
    r'\d{2}\s?BITS?|\d{2,3}\s?KHZ|HI-?RES'  # 24bit / 48kHz / Hi-Res
    r')'
)
_TRAILING_FMT_TAG_RE = re.compile(
    r'\s*[\[\(]\s*' + _FMT_TOKEN +
    r'(?:[\s._/,+-]+' + _FMT_TOKEN + r')*\s*[\]\)]\s*$',
    re.IGNORECASE,
)


def _strip_trailing_format_tags(album: str) -> str:
    """Peel trailing bracketed format/quality tags off an album title.
    Never returns empty: if stripping would erase the whole title, the original
    is kept (defends against an album that is itself just a bracketed token)."""
    cur = album
    while True:
        nxt = _TRAILING_FMT_TAG_RE.sub('', cur).rstrip(' -._')
        if nxt == cur or not nxt:
            break
        cur = nxt
    return cur if cur else album


# Bare (UNbracketed) codec/source tokens that leak to the FRONT of a parsed album
# when a folder is named "<year> FLAC <Title>" (the reader strips the year but not
# the format token) — e.g. "2023 FLAC Warrior" → album "FLAC Warrior". Restricted
# to CODEC / SOURCE / bit-depth tokens that are NEVER a real leading title word, so
# this can't eat a genuine title. Deliberately EXCLUDES CD/VINYL/LP/TAPE/SACD —
# those can plausibly begin a real album name. ~10.7k rows affected (F:\RockMetal).
# Restricted to codec tokens that are NEVER a real leading title word. Deliberately
# EXCLUDES 'opus' (Ghost "Opus Eponymous", Laibach "Opus Dei"), 'web' ("Web of Lies"),
# and 'ape' ("Ape…") — all real title words; their few stray-prefix rows (WEB=3, APE=6)
# aren't worth the false-positive risk. 'flac' is 10.7k of the ~10.7k cases anyway.
# 'ogg'/'opus'/'web'/'ape' EXCLUDED — they are real leading title words ("Ogg Zurthron…",
# "Opus Eponymous", "Web of Lies"); their rare stray-prefix rows aren't worth the false strip.
_LEADING_FMT_TOKENS = frozenset((
    'flac', 'mp3', 'wv', 'wav', 'aac', 'alac', 'm4a', 'dsf', 'dsd', 'webflac',
))
_LEADING_FMT_RE = re.compile(r'^(?:\d{2}bits?|\d{2}[-/]\d{2,3}(?:\.\d)?)$', re.IGNORECASE)


def _strip_leading_format_tokens(album: str) -> str:
    """Drop a leading RUN of bare codec/source/bit-depth tokens from an album title
    ("FLAC Warrior" → "Warrior", "FLAC FLAC Title" → "Title"). Whole-token only;
    NEVER returns empty (an album that is literally just "FLAC" is left untouched)."""
    if not album:
        return album
    toks = album.split()
    i = 0
    while i < len(toks):
        t = toks[i].strip('.-_').lower()
        if t in _LEADING_FMT_TOKENS or _LEADING_FMT_RE.match(t):
            i += 1
        else:
            break
    if i == 0:
        return album                      # nothing leading to strip
    rest = ' '.join(toks[i:]).strip(' -._')
    return rest if rest else album        # never empty


# ── Catalog number from folder name ──────────────────────────────────────────
# Scene/FLAC releases carry the label catalog number in parentheses right after
# the album, e.g.  Aborted-Vault_of_Horrors-(NBR68170)-...-CD-FLAC-2024-86D
# Audio tags almost never have it (1 in ~287k folders in this library), but the
# folder NAME does for ~22% of FLAC folders. Extraction is deliberately
# conservative — a bracketed token only counts as a catalog number when it:
#   * starts with a letter (a label prefix), and
#   * mixes letters and digits, and
#   * is not a format/quality token (16-44, 2CD, 24BIT…), and
#   * is not an edition / media / release-type word (Digipak, Reissue, Disc…), and
#   * carries no standalone 4-digit year chunk (which would mean a token like
#     "Remastered 2011" or "Demo 1995", never a catalog id).
# This keeps the false-positive rate near zero on non-scene names while catching
# the scene convention reliably.
_CATNO_BRACKET_RE = re.compile(r'[\(\[]\s*([^\(\)\[\]]{2,80}?)\s*[\)\]]')  # M8: 40→80; a real [Label, CatNo, Country] tag routinely exceeds 40 chars (lazy + bracket-excluding class, so no over-match)
_CATNO_SHAPE_RE   = re.compile(r'^[A-Za-z0-9][A-Za-z0-9]*(?:[ ._/\-][A-Za-z0-9]+){0,5}$')
# A bare year, a decade placeholder (19XX, 198X, 20XX), or a year glued to a
# release-type word (2025EP, 1999CD) — none are catalog numbers.
_CATNO_YEAR_CHUNK = re.compile(
    r'^(?:19|20)(?:\d|[X#?]){2}$'
    r'|^(?:19|20)\d{2}(?:EP|LP|CD|CDS|MCD|MLP|SP|MX|DEMO)$',
    re.IGNORECASE)
# A bit-depth / sample-rate / bitrate token is a quality tag, never a catalog id
# ("16bit-44.1kHz", "24bit 192kHz", "320kbps").
_CATNO_FMT_GUARD = re.compile(r'\d[\s._\-]*(?:bit|bits|khz|hz|kbps)\b', re.IGNORECASE)
# Trailing media/source suffix that isn't part of the catalog id proper
# ("ALT-001-CD" -> "ALT-001").
_CATNO_MEDIA_SUFFIX_RE = re.compile(
    r'[ ._/\-](CDS|CDM|CDR|CDA|CD|LP|MLP|EP|VINYL|VLS|WEB|TAPE|SACD|DIGI|DIGIPAK|PROMO)$',
    re.IGNORECASE)
# Two-letter country/region codes commonly used in [Label, CatNo, Country] tags.
_COUNTRY_CODES = {
    'US', 'USA', 'UK', 'GB', 'EU', 'DE', 'RU', 'JP', 'JPN', 'FR', 'IT', 'ES',
    'NL', 'SE', 'NO', 'FI', 'PL', 'GR', 'BR', 'CA', 'AU', 'UA', 'BE', 'CH',
    'AT', 'PT', 'DK', 'CZ', 'HU', 'KR', 'CN', 'MX', 'AR', 'CL', 'IE', 'IS', 'TR',
    # full names also appear in the trailing field
    'GERMANY', 'JAPAN', 'RUSSIA', 'ITALY', 'FRANCE', 'SPAIN', 'SWEDEN',
    'NORWAY', 'FINLAND', 'POLAND', 'GREECE', 'BRAZIL', 'CANADA', 'AUSTRALIA',
    'UKRAINE', 'NETHERLANDS', 'BELGIUM', 'SWITZERLAND', 'AUSTRIA', 'PORTUGAL',
    'DENMARK', 'MEXICO', 'SERBIA', 'TURKEY', 'ENGLAND', 'SCOTLAND', 'IRELAND',
    'ICELAND', 'HUNGARY', 'KOREA', 'CHINA', 'ARGENTINA', 'CHILE',
}
# Whole-chunk words that prove a bracket is NOT a catalog number.
_CATNO_REJECT_WORDS = {
    'DIGIPAK', 'DIGI', 'REISSUE', 'REMASTER', 'REMASTERED', 'REMIX', 'REMIXED',
    'DELUXE', 'LIMITED', 'LTD', 'EDITION', 'BONUS', 'ANNIVERSARY', 'EXPANDED',
    'SPECIAL', 'COLLECTORS', 'JAPANESE', 'JAPAN', 'REPRESS', 'REPRINT',
    'INSTRUMENTAL', 'ACOUSTIC', 'LIVE', 'DEMO', 'SINGLE', 'PROMO', 'SAMPLER',
    'COMPILATION', 'SOUNDTRACK', 'OST', 'SPLIT', 'EP', 'BOOTLEG', 'UNCENSORED',
    'EXPLICIT', 'REPACK', 'DISC', 'PART', 'PT', 'VOL', 'VOLUME', 'SIDE', 'TRACK',
    'CD', 'CDS', 'CDR', 'CDM', 'CDA', 'LP', 'MLP', 'VINYL', 'VLS', 'WEB', 'TAPE',
    'CASSETTE', 'SACD', 'EAC', 'MAG',
    # B-L7: audiophile mastering/medium designations — the glued letter+digit
    # forms (K2HD, XRCD24…) are caught by _CATNO_MASTERING_RE below; these word
    # forms cover the separated chunks ('K2HD Mastering', 'SHM CD').
    'MASTERING', 'MASTERED', 'K2HD', 'XRCD', 'SHM', 'SHMCD', 'UHQCD',
    # prose function-words — a catalog token is a code, not a phrase
    'THE', 'OF', 'AND', 'FOR', 'FROM', 'WITH', 'YOUR', 'OUR', 'NEW', 'ONES',
    'FAVORITE', 'TUNES', 'PERFORMED',
}


# Month names + ordinals — a bracket like "(December 13th, 1862)" is a DATE, not
# a "[Label, CatNo, Country]" tag, even though it has commas, letters and digits.
_CATNO_MONTHS = {
    'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'SEPT',
    'OCT', 'NOV', 'DEC', 'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'JUNE',
    'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
}
_CATNO_ORDINAL_RE = re.compile(r'^\d{1,2}(?:ST|ND|RD|TH)$', re.IGNORECASE)
# B-H4: GLUED disc-numbering tokens ('Disc2', 'CD2', 'Vol3') — the reject-words check
# below splits on separators, so the separated forms ('Disc 2') are caught by
# _CATNO_REJECT_WORDS but the glued forms validated as letters+digits "catalogs",
# storing catalog_number='Disc2' and treating disc 2 as a distinct pressing.
_CATNO_DISCNUM_RE = re.compile(r'^(?:DISC|DISK|CD|PART|PT|VOL|VOLUME|SIDE)\.?\d{1,2}$',
                               re.IGNORECASE)
# B-L7: GLUED audiophile mastering/medium tokens (K2HD, XRCD2/XRCD24, DSD64/128/
# 256, BSCD2, SHMCD, UHQCD) — they mix letters+digits so they validated as
# "catalogs", storing junk catalog data and treating the copy as a distinct
# pressing forever (over-keep; also blocked the real NFO catalog via the
# gap-fill-only rule). Like _CATNO_DISCNUM_RE this is checked per separator
# chunk, so '[XRCD24]' and '[K2HD Mastering]' both reject while real catalogs
# ('WOLF-075', 'VICP-60153', 'DMP0257') keep their letter+digit chunks.
_CATNO_MASTERING_RE = re.compile(
    r'^(?:K2HD|XRCD\d{0,2}|DSD\d{2,3}|BSCD\d?|SHM-?CD|UHQCD)$', re.IGNORECASE)

# Classical WORK-catalog designations (opus/thematic-catalog numbers) — these identify a
# composition, NOT a label release. '(Op 67)', '(BWV 1043)', '(K 550)' would otherwise pass
# _clean_catno (letters+digits, 4-16 alnum) and be stripped from the album, erasing the
# work distinction. Reject them as catalog candidates. (audit R3 [8])
_CATNO_WORK_RE = re.compile(
    r'^(?:OP|BWV|KV|WOO|HWV|RV|HOB|BB|SZ|DEUTSCH)\s*\.?\s*\d{1,4}[a-z]?$', re.IGNORECASE)


def _clean_catno(tok: str, require_letter: bool = True) -> str:
    """Validate/normalize one candidate catalog token. Returns the cleaned id or
    ''. require_letter=False permits pure-numeric ids — only pass that inside a
    high-confidence [Label, CatNo, Country] context (a country code present)."""
    tok = (tok or '').strip()
    # Peel a trailing media suffix BEFORE validating ("ALT-001-CD" -> "ALT-001").
    tok = _CATNO_MEDIA_SUFFIX_RE.sub('', tok).strip(' ._/-') or tok
    if not _CATNO_SHAPE_RE.match(tok) or _CATNO_FMT_GUARD.search(tok):
        return ''
    if _CATNO_WORK_RE.match(tok):        # classical opus/work number, not a label catalog
        return ''
    letters = sum(c.isalpha() for c in tok)
    digits  = sum(c.isdigit() for c in tok)
    alnum   = sum(c.isalnum() for c in tok)
    if digits < 1 or alnum < 4 or alnum > 16:
        return ''
    if require_letter and letters < 2:
        return ''
    for ch in re.split(r'[ ._/\-]+', tok):
        if (_CATNO_YEAR_CHUNK.match(ch) or _CATNO_ORDINAL_RE.match(ch)
                or _CATNO_DISCNUM_RE.match(ch) or _CATNO_MASTERING_RE.match(ch)   # B-L7
                or ch.upper() in _CATNO_REJECT_WORDS or ch.upper() in _CATNO_MONTHS):
            return ''
    return tok


_LABEL_JUNK_RE = re.compile(
    r'^(?:(?:19|20)\d{2}|\d?CD|\d?LP|EP|2CD|3CD|\d{2}[-/]\d{2,3}|'
    r'\d{2}\s?BIT|\d{2,3}\s?KHZ|WEB|FLAC|MP3|VINYL|PROMO|DIGIPAK)$', re.IGNORECASE)


def _sanitize_label(label: str) -> str:
    """Blank a 'label' field that is actually a year, a disc/format token, or a
    bare number — those land in the label slot of a 2-field bracket but aren't a
    real label."""
    s = (label or '').strip()
    if not s or s.isdigit() or _LABEL_JUNK_RE.match(s):
        return ''
    return s


def parse_release_meta_from_name(folder_name: str) -> dict:
    """Extract {catalog, label, country} from a folder name. Two shapes:
      A) comma tag  "[Label, CatNo, Country]"  — also yields label + country and
         allows a pure-numeric catalog (the surrounding fields disambiguate);
      B) scene parens  "...-(WOLF-075)-CD-FLAC-2018-GRP"  — bare catalog token.
    Conservative: returns empties when nothing is confidently a catalog id."""
    out = {'catalog': '', 'label': '', 'country': ''}
    if not folder_name:
        return out
    # Pattern A — comma-delimited [Label, CatNo, (Country)]
    _partial = None   # L93: best country-anchored (label,country) seen with NO valid catalog
    for m in _CATNO_BRACKET_RE.finditer(folder_name):
        inner = m.group(1)
        if ',' not in inner:
            continue
        parts = [p.strip() for p in inner.split(',') if p.strip()]
        # A leading pressing-year field — "[1998, Victor, VICP-60153, Japan]" =
        # [Year, Label, CatNo, Country] — is common; drop it so the remaining
        # Label/CatNo/Country parse normally (otherwise the 4-field tag is skipped
        # and the catalog is lost, mis-collapsing distinct pressings as dupes).
        if len(parts) == 4 and re.fullmatch(r'(?:19|20)\d{2}', parts[0]):
            parts = parts[1:]
        if not (2 <= len(parts) <= 3):
            continue
        # A country code can sit at either end ([Label, CatNo, Country] or
        # [Country, CatNo, Label]).
        country = ''
        if parts[-1].upper() in _COUNTRY_CODES:
            country = parts[-1].upper(); parts = parts[:-1]
        elif parts[0].upper() in _COUNTRY_CODES:
            country = parts[0].upper(); parts = parts[1:]
        # A pure-numeric catalog ("2602-2", "19439890592") is only trustworthy
        # when a country code anchors the tag; without one, require a real
        # alphanumeric catalog so dates/locations ("December 13th, 1862") fall
        # through.
        rl = not bool(country)
        label, cand = '', ''
        if len(parts) == 1:
            cand = parts[0]
        elif len(parts) >= 2:
            a, b = parts[0], parts[1]
            # The catalog is whichever field validates as one; the other is the
            # label (handles both [Label, CatNo] and [CatNo, Label] orders).
            if _clean_catno(b, require_letter=rl) and not _clean_catno(a, require_letter=rl):
                label, cand = a, b
            elif _clean_catno(a, require_letter=rl) and not _clean_catno(b, require_letter=rl):
                label, cand = b, a
            else:
                label, cand = a, b
        cat = _clean_catno(cand, require_letter=rl)
        if cat:
            out.update(catalog=cat, label=_sanitize_label(label), country=country)
            return out
        # L93: no valid catalog, but the COUNTRY CODE is a high-confidence anchor — remember
        # the (label, country) so a tag like "[Nuclear Blast, Germany]" still yields country +
        # label (the contract says Pattern A "also yields label + country"). _sanitize_label
        # blanks junk; `label` is '' in the single-field-after-country case, so never junk.
        if country and _partial is None:
            _partial = {'catalog': '', 'label': _sanitize_label(label), 'country': country}
    if _partial is not None:
        return _partial
    # Pattern B — single-token (scene) bracket
    for m in _CATNO_BRACKET_RE.finditer(folder_name):
        tok = m.group(1).strip()
        if ',' in tok:
            continue
        cat = _clean_catno(tok, require_letter=True)
        if cat:
            out['catalog'] = cat
            return out
    return out


def parse_catalog_from_name(folder_name: str) -> str:
    """Best-effort label catalog number from a folder name, or '' if none."""
    return parse_release_meta_from_name(folder_name)['catalog']


def normalize_catalog(catno: str) -> str:
    """Comparison key for catalog numbers: uppercase, drop non-alphanumerics so
    'ALT-001' and 'alt 001' compare equal."""
    return re.sub(r'[^A-Za-z0-9]', '', catno or '').upper()


def _strip_catalog_bracket(album: str, catalog: str) -> str:
    """Remove from an album the [Label, CatNo, Country] / (CatNo) bracket whose
    catalog number we've already parsed out, so it doesn't pollute the dedup
    match key (e.g. 'Vol. 4 [Warner Bros. Records, 2602-2, US]' -> 'Vol. 4',
    'Album (ABC-123)' -> 'Album'). Only a bracket whose normalized content
    CONTAINS the parsed catalog is removed — unrelated brackets like '[Live]' or
    '(Remastered)' are left untouched. Never empties the album."""
    if not album or not catalog:
        return album
    ncat = normalize_catalog(catalog)
    if not ncat:
        return album

    def _field_is_cat(field):
        # The catalog must be the WHOLE field (or whole bracket), not an arbitrary
        # substring — else a short catalog ('CAT100') wrongly strips an unrelated bracket
        # whose text merely contains it ('(Bonus CAT1009 extra)'). A trailing media suffix
        # ('CAT100 CD') is peeled first so the real label-tag field still matches.
        if normalize_catalog(field) == ncat:
            return True
        peeled = _CATNO_MEDIA_SUFFIX_RE.sub('', (field or '').strip()).strip(' ._/-')
        return bool(peeled) and normalize_catalog(peeled) == ncat

    def _repl(m):
        content = m.group(1)
        if _field_is_cat(content):
            return ''                                    # bare (CatNo) bracket
        if ',' in content and any(_field_is_cat(f) for f in content.split(',')):
            return ''                                    # [Label, CatNo, Country] tag
        return m.group(0)

    out = re.sub(r'\s*[\(\[]([^\(\)\[\]]*)[\)\]]', _repl, album).strip(' -_')
    return out or album



# Backtick-delimited uploader releases use the rigid form
#   "Artist`YEAR`Album by <uploader>"   e.g. "Abrogation`2026`Widerschein by necroscum"
# The backtick is not one of the standard separators, so without a dedicated
# parser the whole string lands in `album` (the year is the only thing pulled
# out), polluting ~750 folders. The trailing " by <uploader>" is an upload
# signature, never part of the real title, so it is stripped here. This strip is
# applied ONLY inside the backtick branch where the format guarantees it.
_UPLOADER_SUFFIX_RE = re.compile(r'\s+by\s+\S+\s*$', re.IGNORECASE)


def _parse_backtick(folder_name: str) -> dict | None:
    """Parse the uploader format "Artist`YEAR`Album by uploader".

    Fires ONLY when a backtick-delimited segment is *exactly* a 4-digit year —
    that bare-year-between-backticks is the upload signature. This guard is
    essential: a backtick is also commonly used as a stand-in apostrophe inside
    titles (e.g. "Come An`Get It", "You Can`t Stop Rock `n` Roll"), and those
    must NOT be treated as artist/album separators. In the apostrophe cases no
    segment is a standalone year, so this returns None and normal parsing runs.
    """
    parts = [p.strip() for p in folder_name.split('`')]
    parts = [p for p in parts if p]
    if len(parts) < 3:
        return None
    # Require an interior segment that is exactly a 4-digit year.
    year, year_idx = '', -1
    for i, p in enumerate(parts):
        if 0 < i < len(parts) - 1 and re.fullmatch(r'(?:19|20)\d{2}', p):
            year, year_idx = p, i
            break
    if year_idx == -1:
        return None
    artist = ' '.join(parts[:year_idx]).strip()
    album  = ' '.join(parts[year_idx + 1:]).strip()
    album = _UPLOADER_SUFFIX_RE.sub('', album).strip(' -[]()_')
    if not artist or not album:
        return None
    return {'artist': artist, 'album': album, 'year': year}


def parse_folder_name(folder_name: str, parent_name: str = '',
                      folder_path=None, music_files: list = None) -> dict:
    """
    Try to extract {artist, album, year} from a folder name.
    parent_name:  the immediate parent folder name (may be the artist).
    folder_path:  Path object — used to scan filenames for scene releases.
    music_files:  optional list of music file names (saves a disk read).
    Returns dict with keys: artist, album, year, is_scene, parse_confidence
    """
    result = {'artist': '', 'album': '', 'year': '', 'is_scene': False,
              'parse_confidence': 'low',
              'catalog_number': '', 'label': '', 'country': ''}

    # Catalog number / label / country are derived from the RAW name (scene puts
    # the catalog in parens after the album; many rips use "[Label, CatNo, CC]").
    # Set them up-front so every return path below carries them — the
    # scene/backtick branches return early.
    _rel = parse_release_meta_from_name(folder_name)
    result['catalog_number'] = _rel['catalog']
    result['label']          = _rel['label']
    result['country']        = _rel['country']

    if is_scene_release(folder_name):
        result['is_scene'] = True
        parsed = _parse_scene(folder_name, folder_path, music_files)
        result.update(parsed)
        # Only HIGH-confidence when the scene parse actually yielded an album. If every
        # pre-year segment was a format tag, album comes back '' — locking that in as
        # high-confidence would bypass the audio-tag fallback and let distinct empty-album
        # folders pool under dedup_album_key(''). Downgrade so tags can supply the album.
        result['parse_confidence'] = 'high' if (result.get('album') or '').strip() else 'low'
        return result

    # Backtick-delimited uploader format ("Artist`YEAR`Album by uploader").
    # Deterministic and unambiguous, so trust it at high confidence.
    if '`' in folder_name:
        bt = _parse_backtick(folder_name)
        if bt:
            result.update(bt)
            result['parse_confidence'] = 'high'
            return result

    # Clean: replace underscores, strip known suffixes. Never let the strip
    # empty the name (a title that IS a format word, e.g. 'CD'): keep the
    # pre-strip form in that case so the album isn't lost.
    clean = folder_name.replace('_', ' ').strip()
    stripped = _SUFFIX_STRIP.sub('', clean).strip()
    clean = stripped if stripped else clean

    # Remove the [Label, CatNo, Country] / (CatNo) bracket we already parsed out
    # BEFORE pattern-matching — otherwise a hyphen INSIDE the catalog ("ABC-123")
    # is mistaken for the artist/album separator, and the tag pollutes the dedup
    # key. Done here (not after) so artist/album split on the right boundary.
    if result['catalog_number']:
        clean = _strip_catalog_bracket(clean, result['catalog_number']) or clean

    # B-L47: peel trailing bracketed format/quality tags BEFORE the pattern loop
    # — a bare "King of Gods [WEB] [16-44]" (no ' - ') otherwise splits at the
    # hyphen INSIDE the bracket via the bare-hyphen fallback (artist='King of
    # Gods [WEB] [16', album='44]'). Guarded never-empty; the post-loop strip on
    # result['album'] stays (a sub-captured album can re-expose a tag) and a
    # second application is an idempotent no-op.
    clean = _strip_trailing_format_tags(clean)

    # Decide if parent is an artist folder (used to prefer Year-Album interpretation)
    parent_clean_full = parent_name.replace('_', ' ').strip() if parent_name else ''
    parent_is_artist = bool(parent_clean_full) and _looks_like_artist_folder(parent_clean_full)

    # Year-organised discography packs nest as ".../<Year> - <Album>/<Year> - <Album> [edition]".
    # There the parent is NOT an artist (it has a year), so the gate below would
    # skip Year-Album and mis-read the leading YEAR as the ARTIST (e.g. Slipknot's
    # "2001 - Iowa [...]" -> artist "2001", later fuzzy-matched to band "200%").
    # Detect that case: the folder's own leading 4-digit year also begins the parent.
    _lead = re.match(r'^((?:19|20)\d{2})\b', clean)
    leading_year = _lead.group(1) if _lead else ''
    leading_year_matches_parent = bool(leading_year) and parent_clean_full.startswith(leading_year)

    for pattern, groups in _FOLDER_PATTERNS:
        # Skip Year-Album pattern unless the parent looks like an artist OR the
        # folder's leading year also starts the parent (a year-nested pack).
        # (Guards against mis-reading an ordinary "Artist - Album" as year=Artist,
        # while still catching year-prefixed discography folders.)
        if ('year' in groups and list(groups.keys()) == ['year', 'album']
                and not parent_is_artist and not leading_year_matches_parent):
            continue
        m = pattern.match(clean)
        if m:
            vals = {field: m.group(grp).strip() for field, grp in groups.items()}
            # L92: the B-M33 4th segment — re-inject an EDITION/TYPE word into the album (so the
            # remaster/deluxe/reissue stays distinct in dedup), but drop a real scene-GROUP tag.
            _tail = vals.pop('_tail', '')
            if _tail and _tail.lower() in _SCENE_TAIL_KEEP and vals.get('album'):
                vals['album'] = (vals['album'] + ' ' + _tail).strip()
            # Reject a match that captured an EMPTY album — e.g. "Artist - (2001) Album"
            # mis-matched by an "Artist - Album (Year)" pattern, whose lazy album group
            # collapses to ''. Accepting it set album='' → later overwritten with the
            # WHOLE folder name (artist duplicated in, real album lost). Skip instead.
            if 'album' in vals and not vals['album']:
                continue
            # B-L46: reject a Year-Album match whose captured album reduces to
            # BRACKET-ONLY content — a year-titled album with an edition suffix
            # ('1916 [Remaster]' under an artist parent) would store
            # album='[Remaster]' and lose the title. Fall through to the next
            # pattern / whole-name fallback; do NOT substitute the year as the
            # album (that would mislabel real '1993 - Promo (Demo)' folders).
            if (list(groups.keys()) == ['year', 'album']
                    and not re.sub(r'[\(\[][^\)\]]*[\)\]]', '',
                                   vals.get('album', '')).strip(' -–_.')):
                continue
            result.update(vals)
            result['parse_confidence'] = 'medium'
            break

    # A purely-numeric "artist" is never a real artist — almost always a bare
    # leading year ("2024 - Album") that mis-split as the artist because the
    # parent isn't an artist folder and it wasn't recognised as a year-pack.
    # Drop it (recording the year if we have none) so the parent-folder / audio-
    # tag fallbacks supply the true artist instead of poisoning identity with a
    # number that then fuzzy-collides with real bands (the "2001"→"200%" class).
    # Only drop a value that actually looks like a YEAR (19xx/20xx) — the bare-year
    # mis-split this guards against. A blanket `.isdigit()` clear also erased real
    # all-numeric bands (1349, 311, 3); those don't match a year and are KEPT.
    # B-L45: a BRACKETED year ('[1999]', '(1996)') evaded this guard (fullmatch
    # on the raw string), keeping a bogus artist that also suppressed the
    # parent-folder fallback below. Strip exactly ONE enclosing bracket pair
    # before the year-shape test — never all punctuation ('20/20' must not
    # false-match a year).
    if result['artist']:
        _abm = re.fullmatch(r'[\(\[]\s*([^\(\)\[\]]+?)\s*[\)\]]', result['artist'])
        _atest = _abm.group(1) if _abm else result['artist']
        if re.fullmatch(r'(?:19|20)\d{2}', _atest):
            if not result['year']:
                result['year'] = _atest
            result['artist'] = ''

    # If no artist found and parent looks like an artist folder, use it
    if not result['artist'] and parent_name:
        parent_clean = parent_name.replace('_', ' ').strip()
        if _looks_like_artist_folder(parent_clean):
            result['artist'] = parent_clean
            result['parse_confidence'] = 'medium'

    # Fall back: treat whole cleaned name as album
    if not result['album']:
        result['album'] = clean

    # Strip trailing bracketed format/quality tags ("[WEB] [16-44]") from the
    # album so they don't pollute matching/dedup. Guarded to never empty it.
    # (The catalog bracket was already removed from `clean` above, before the
    # artist/album split.)
    if result['album']:
        result['album'] = _strip_trailing_format_tags(result['album'])

    # Strip a stray LEADING codec token ("FLAC Warrior" → "Warrior") that a
    # "<year> FLAC <Title>" folder leaves on the front of the album. Guarded to
    # never empty it. Without this, a rescan would re-introduce the junk prefix
    # that backfill_flac.py cleaned (~10.7k F:\RockMetal rows).
    if result['album']:
        result['album'] = _strip_leading_format_tokens(result['album'])

    # Extract year from album string if still missing.
    if not result['year'] and result['album']:
        m = _YEAR_RE.search(result['album'])
        if m:
            yr = m.group(1)
            result['year'] = yr
            # Only REMOVE the year from the album when it's a TRAILING annotation
            # preceded by a delimiter ("Album (2024)", "Album - 2024", "Album
            # [2024]"). Never strip an INTERIOR year — doing so splices unrelated
            # words ("Live 1989 Reissue" → "Live  Reissue") — and never strip a
            # year glued into prose by a plain space ("Top 100 Hits of 2024" must
            # keep its title). Titles that ARE a year ('1916','1984') also survive
            # since the strip only fires on a delimiter, not a bare-year title.
            trail = re.search(r'[\(\[\-–]\s*' + re.escape(yr) + r'\s*[\)\]]?\s*$',
                              result['album'])
            if trail:
                stripped_album = result['album'][:trail.start()].strip(' -–[]()_')
                if stripped_album:
                    result['album'] = stripped_album

    return result


def _looks_like_artist_folder(name: str) -> bool:
    """True if name looks like a plain artist folder (no year, no format tags)."""
    if _YEAR_RE.search(name):
        return False
    if re.search(r'\b(FLAC|MP3|WEB|CD|VINYL)\b', name, re.IGNORECASE):
        return False
    return True


_SCENE_TAG_RE = re.compile(
    r'^(WEB|WEBRIP|WEBFLAC|CD|CDM|CDR|CDS|CDRIP|CDA|CDDA|CDEP|VINYL|LP|MLP|VLS|EP|MCD|'
    r'7INCH|10INCH|12INCH|'
    r'\d+CD|\dCD|\d+BIT|'
    r'FLAC|MP3|AAC|M4A|OGG|APE|WAV|ALAC|'
    r'PROMO|BOOTLEG|BTLG|SAMPLER|REPACK|RERIP|REMASTER|REMASTERED|RETAIL|REISSUE|'
    r'READNFO|PROPER|DIGIPAK|SACD|DVD|DVDA|TAPE|CASSETTE|'
    r'LIMITED|DELUXE|BOXSET|VBR|CBR|320|256|192|128)$',
    re.IGNORECASE
)

# M7: the strip-before-year loop must drop ONLY format/source/codec tokens — NOT release
# TYPE/EDITION words (EP, REMASTER(ED), REISSUE, LIMITED, DELUXE, BOXSET, PROMO, BOOTLEG,
# SAMPLER), which distinguish editions and must survive into the album (mirrors _DEDUP_KEEP
# and the documented "keep release-type words" invariant). Stripping them made a scene EP
# collide with the full-length, and erased remaster/reissue/deluxe distinctions at parse time.
_SCENE_FMT_RE = re.compile(
    r'^(WEB|WEBRIP|WEBFLAC|CD|CDM|CDR|CDS|CDRIP|CDA|CDDA|CDEP|VINYL|LP|MLP|VLS|MCD|'
    r'7INCH|10INCH|12INCH|'
    r'\d+CD|\dCD|\d+BIT|'
    r'FLAC|MP3|AAC|M4A|OGG|APE|WAV|ALAC|'
    r'REPACK|RERIP|RETAIL|READNFO|PROPER|DIGIPAK|SACD|DVD|DVDA|TAPE|CASSETTE|'
    r'VBR|CBR|320|256|192|128)$',
    re.IGNORECASE
)

def _extract_artist_from_filenames(music_files: list) -> str:
    """
    Scene file format: 'NN-artist-track_title.ext' or 'NNN-artist-track_title.ext'.
    If most files agree on the second dash-separated chunk, that's the artist.
    Returns canonical artist name (with underscores → spaces), or '' if no consensus.
    """
    if not music_files:
        return ''

    from collections import Counter
    candidates = Counter()
    for fname in music_files:
        # strip extension
        stem = re.sub(r'\.[^.]+$', '', fname)
        parts = stem.split('-', 2)   # max 3 parts: track, artist, title
        if len(parts) >= 3:
            track, artist, _ = parts
            # Validate: first chunk should be a track number (1-3 digits, maybe with disc prefix)
            if re.match(r'^\d{1,3}$', track.strip()) or re.match(r'^d\d+_?\d+$', track.strip(), re.IGNORECASE):
                candidates[artist.strip().lower()] += 1

    if not candidates:
        return ''

    # Require BOTH an absolute floor (≥2 files) AND a proportional majority of
    # the files that have the NN-artist-title shape. A bare count>=2 was too
    # weak: in a 30-track album where 28 files have unique middle chunks but 2
    # coincidentally share one (e.g. '29-live-bonus', '30-live-encore'), the
    # shared chunk 'live' would win with just 2/30 files and get mistaken for
    # the artist. Demanding a majority of the dash-formatted files agree makes
    # this robust while still accepting genuine scene releases (where nearly
    # every track carries the same artist chunk).
    top, count = candidates.most_common(1)[0]
    total_dashed = sum(candidates.values())
    if count < 2:
        return ''
    # L96: a real scene artist is never all digits. An all-numeric consensus winner is a
    # misread track number from a dash-separated disc-track prefix ('1-01-Title' → track='1'
    # passes, artist='01') — reject it so the folder-name / tag fallbacks supply the true
    # artist instead of poisoning identity with a bare number. (Numeric real bands like 1349
    # are parsed via the non-scene _FOLDER_PATTERNS path, not this helper.)
    if re.fullmatch(r'\d+', top.strip()):
        return ''
    if total_dashed >= 4 and count <= total_dashed * 0.5:
        # Enough dash-formatted files to judge proportion, but no STRICT majority
        # agrees (a 50/50 tie is ambiguous — reject rather than guess).
        return ''

    # Return the most-seen artist, with underscores replaced
    return top.replace('_', ' ').strip()


def _list_music_filenames(folder_path) -> list:
    """Return music filenames in folder (best-effort)."""
    if folder_path is None:
        return []
    try:
        from pathlib import Path
        p = Path(folder_path) if not isinstance(folder_path, Path) else folder_path
        return [f.name for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in {'.mp3','.flac','.m4a','.ogg','.opus','.wav','.ape','.aac','.alac','.wv'}]
    except Exception:
        return []


def _parse_scene(folder_name: str, folder_path=None, music_files: list = None) -> dict:
    """
    Parse a scene-format folder: Artist_Name-Album_Name-[TAGS-]YEAR-GROUP
    Strategy:
      1. Try to extract artist from track filenames (most reliable for scene)
      2. Strip parens, format tags, year, group from folder name
      3. Whatever remains, minus the artist prefix, is the album
    """
    # ── Step 1: filename-based artist extraction ──────────────────────────
    if music_files is None and folder_path is not None:
        music_files = _list_music_filenames(folder_path)
    filename_artist = _extract_artist_from_filenames(music_files or [])

    # ── Step 2: clean the folder name ─────────────────────────────────────
    cleaned = re.sub(r'\([^)]*\)|\[[^\]]*\]', '', folder_name)
    cleaned = re.sub(r'-+', '-', cleaned).strip('-')

    parts = cleaned.split('-')
    year  = ''
    year_idx = -1
    for i, p in enumerate(parts):
        if re.match(r'^(?:19|20)\d{2}$', p):
            year = p
            year_idx = i
            break

    if year_idx == -1:
        return {'album': folder_name.replace('_', ' '), 'year': '', 'artist': filename_artist}

    # Strip trailing format/source tags before the year — but KEEP release TYPE/EDITION words
    # (EP/REMASTER/REISSUE/LIMITED/DELUXE/…) so distinct editions don't collapse (M7).
    before_year = parts[:year_idx]
    while before_year and _SCENE_FMT_RE.match(before_year[-1].strip()):
        before_year.pop()

    # ── Step 3: split artist/album ────────────────────────────────────────
    if filename_artist:
        # We know the artist — strip its parts from the front, rest is album
        artist_words = filename_artist.lower().split()
        artist_parts_count = 0
        joined = ''
        for i, part in enumerate(before_year):
            joined = (joined + ' ' + part.replace('_', ' ')).strip().lower()
            joined = re.sub(r'\s+', ' ', joined)
            if joined == filename_artist.lower() or joined.replace(' ', '') == filename_artist.replace(' ', '').lower():
                artist_parts_count = i + 1
                break

        if artist_parts_count > 0:
            album_parts = before_year[artist_parts_count:]
            album       = ' '.join(p.replace('_', ' ') for p in album_parts).strip()
            album       = re.sub(r'\s+', ' ', album)
            return {'artist': filename_artist.title(),
                    'album':  album,
                    'year':   year}
        # Filename artist found but doesn't align to any folder prefix. L95: don't dump EVERY
        # before_year part (artist + album) into the album — that duplicated the artist string.
        # Peel a leading artist run with the dash-splitter, but keep the (more reliable)
        # filename artist; fall back to the full join only if the splitter yields nothing.
        _fa, _split_album = _split_artist_album(before_year)
        album = _split_album or ' '.join(p.replace('_', ' ') for p in before_year).strip()
        album = re.sub(r'\s+', ' ', album)
        return {'artist': filename_artist.title(),
                'album':  album,
                'year':   year}

    # No filename artist — fall back to dash-based splitting with MA
    artist, album = _split_artist_album(before_year)
    return {'artist': artist, 'album': album, 'year': year}


def _split_artist_album(parts: list) -> tuple:
    """
    Given the dash-separated parts before YEAR (with format tags stripped),
    decide where the artist/album boundary is. Try MA lookup to disambiguate
    when there are 3+ parts.
    """
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0].replace('_', ' ').strip(), ''
    if len(parts) == 2:
        return (parts[0].replace('_', ' ').strip(),
                parts[1].replace('_', ' ').strip())

    # 3+ parts: ambiguous. The Tag & Rename tool asked its offline Metal Archives index which split
    # names a real band; MetalLib has no such index, so it keeps the default.
    # Default: last part = album, rest = artist
    return (' '.join(p.replace('_', ' ') for p in parts[:-1]).strip(),
            parts[-1].replace('_', ' ').strip())


# -- MetalLib additions ------------------------------------------------------------------------------

_DISC_DIR_RE = re.compile(r'\b(?:CD|DVD|Disc|Disk)\s*\d+\b', re.I)
_BAND_COUNTRY_RE = re.compile(r'^(.*?)\s*\(([A-Z]{2,3})\)$')     # the library's "Aghar (IT)" artist folders
_MEDIA_TOKENS = (('Digital', {'web', 'webflac', 'webrip', 'digital', 'bandcamp'}),
                 ('Vinyl', {'vinyl', 'lp', 'mlp', 'vls', '2lp', '12inch', '7inch', '10inch'}),
                 ('Cassette', {'tape', 'cassette', 'mc'}),
                 ('CD', {'cd', 'cdr', 'cdm', 'cds', 'mcd', 'cdda', 'sacd', '2cd', '3cd', '4cd'}))


def media_from_name(name):
    """Media named by a token of the folder name ("-CD-", "_WEB_", "[SOM 532 LP]") or ''."""
    tokens = {t.lower() for t in re.split(r'[\s\-_.\[\]()]+', name or '') if t}
    for kind, words in _MEDIA_TOKENS:
        if tokens & words:
            return kind
    return ''


_TYPE_WORDS = {'EP': 'EP', 'MCD': 'EP', 'SINGLE': 'Single', 'SPLIT': 'Split', 'DEMO': 'Demo',
               'LIVE': 'Live', 'COMPILATION': 'Compilation', 'BOOTLEG': 'Bootleg', 'PROMO': 'Promo',
               'REISSUE': 'Reissue', 'REMASTER': 'Remaster', 'REMASTERED': 'Remaster', 'BOXSET': 'Boxset',
               'LIMITED': 'Limited', 'LTD': 'Limited', 'DELUXE': 'Deluxe'}


def lift_scene_tokens(folder_name):
    """Scene groups put country codes and release-type words as their own dash segments before the
    year ("Crom-Uj_Vilag_Szuletese-HU-WEB-2023-FiH", "Sherane-sherane-EP-WEB-2026-ENTiTLED"). The
    ported parser keeps type words in the album on purpose (the Tag & Rename tool's dedup needs
    them), so the album came out as "HU" / "EP". For searching they get in the way: lift them out
    (never the first segment, never after the year) and return them as hints.
    -> (cleaned name, country, release type)."""
    if not is_scene_release(folder_name):
        return folder_name, '', ''
    parts = folder_name.split('-')
    year_i = next((i for i, p in enumerate(parts) if re.fullmatch(r'(?:19|20)\d{2}', p)), len(parts))
    keep, country, rtype = [], '', ''
    for i, part in enumerate(parts):
        up = part.upper().strip('_ ')
        if 0 < i < year_i and up in _COUNTRY_CODES and len(up) <= 3:
            country = country or up
        elif 0 < i < year_i and up in _TYPE_WORDS and i > 1:
            rtype = rtype or _TYPE_WORDS[up]
        else:
            keep.append(part)
    return '-'.join(keep), country, rtype


_cache = {}


def folder_hints(file_path):
    """What a file's folder name says: {artist, album, year, catalog, label, country, band_country,
    media, is_scene, confidence}. Disc sub-folders ("CD1") are skipped. Cached per folder."""
    folder = Path(file_path).parent
    if _DISC_DIR_RE.search(folder.name) and len(folder.name) <= 12:
        folder = folder.parent
    key = str(folder)
    if key in _cache:
        return _cache[key]
    try:
        music = [f for f in _list_music_filenames(folder)]
    except Exception:
        music = []
    cleaned, scene_country, release_type = lift_scene_tokens(folder.name)
    r = parse_folder_name(cleaned, parent_name=folder.parent.name, music_files=music)
    artist, band_country = r.get('artist') or '', ''
    m = _BAND_COUNTRY_RE.match(artist)
    if m:
        artist, band_country = m.group(1).strip(), m.group(2)
    hints = {'artist': artist, 'album': r.get('album') or '', 'year': r.get('year') or '',
             'catalog': r.get('catalog_number') or '', 'label': r.get('label') or '',
             'country': r.get('country') or scene_country, 'band_country': band_country,
             'release_type': release_type,
             'media': media_from_name(folder.name), 'is_scene': bool(r.get('is_scene')),
             'confidence': r.get('parse_confidence') or 'low'}
    _cache[key] = hints
    return hints

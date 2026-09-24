# MetalLib naming -- ASCII names the way the user's library has them. The tables and rules are
# ported from the user's own "Tag & Rename" tool (tag_reader.transliterate_ascii and
# _safe_path_component), so names MetalLib writes match the existing library: Cyrillic/Greek
# transliterated (not "____"), ":" -> " -", "?" dropped, whitespace collapsed, trailing dots
# dropped from artist/album names ("D.R.I", "Solitude with the Eternal").
#
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import re
import unicodedata as _ud


_ARTIST_TRANSLIT = str.maketrans({
    'ø': 'o', 'æ': 'ae', 'œ': 'oe', 'ð': 'd', 'þ': 'th', 'ł': 'l', 'đ': 'd',
    'ß': 'ss', 'ı': 'i', 'ŋ': 'n', 'ħ': 'h', 'ĸ': 'k', 'ʒ': 'z',
})

# UPPERCASE non-decomposing letters (NFKD leaves these too) + stylization symbols bands use
# that have an obvious ASCII letter equivalent. Applied to the DISPLAY/OUTPUT name (not the
# lowercased artist_key) — § is a "section sign" used as a stylized S (Accu§er → Accuser).
_SYMBOL_TRANSLIT = str.maketrans({
    '§': 's',
    'Ø': 'O', 'Æ': 'AE', 'Œ': 'OE', 'Ð': 'D', 'Þ': 'Th', 'Ł': 'L', 'Đ': 'D',
    'ẞ': 'SS', 'Ŋ': 'N', 'Ħ': 'H', 'ĸ': 'K', 'Ʒ': 'Z',
    '×': 'x', '÷': '-',
})

# Cyrillic → Latin (BGN/PCGN + Ukrainian/Serbian extras)
_CYRILLIC_TO_LATIN: dict = {
    'А': 'A',  'Б': 'B',  'В': 'V',  'Г': 'G',  'Д': 'D',
    'Е': 'E',  'Ё': 'Yo', 'Ж': 'Zh', 'З': 'Z',  'И': 'I',
    'Й': 'Y',  'К': 'K',  'Л': 'L',  'М': 'M',  'Н': 'N',
    'О': 'O',  'П': 'P',  'Р': 'R',  'С': 'S',  'Т': 'T',
    'У': 'U',  'Ф': 'F',  'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch',
    'Ш': 'Sh', 'Щ': 'Shch','Ъ': '',  'Ы': 'Y',  'Ь': '',
    'Э': 'E',  'Ю': 'Yu', 'Я': 'Ya',
    # Ukrainian
    'Є': 'Ye', 'І': 'I',  'Ї': 'Yi', 'Ґ': 'G',
    # Serbian/Macedonian extras
    'Ђ': 'Dj', 'Ж': 'Zh', 'Ѕ': 'Dz', 'Љ': 'Lj', 'Њ': 'Nj',
    'Ћ': 'C',  'Џ': 'Dz', 'Ѣ': 'Ye',
}
# Add lowercase counterparts automatically
_CYRILLIC_TO_LATIN.update({k.lower(): v.lower() for k, v in list(_CYRILLIC_TO_LATIN.items())})

# Greek → Latin (modern monotonic)
_GREEK_TO_LATIN: dict = {
    'Α': 'A', 'Β': 'V', 'Γ': 'G', 'Δ': 'D', 'Ε': 'E', 'Ζ': 'Z',
    'Η': 'I', 'Θ': 'Th','Ι': 'I', 'Κ': 'K', 'Λ': 'L', 'Μ': 'M',
    'Ν': 'N', 'Ξ': 'X', 'Ο': 'O', 'Π': 'P', 'Ρ': 'R', 'Σ': 'S',
    'Τ': 'T', 'Υ': 'Y', 'Φ': 'F', 'Χ': 'Ch','Ψ': 'Ps','Ω': 'O',
    # Accented uppercase
    'Ά': 'A', 'Έ': 'E', 'Ή': 'I', 'Ί': 'I', 'Ό': 'O', 'Ύ': 'Y', 'Ώ': 'O',
    'Ϊ': 'I', 'Ϋ': 'Y',
    # Lowercase
    'α': 'a', 'β': 'v', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z',
    'η': 'i', 'θ': 'th','ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm',
    'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
    'τ': 't', 'υ': 'y', 'φ': 'f', 'χ': 'ch','ψ': 'ps','ω': 'o',
    'ς': 's',  # final sigma
    # Accented lowercase
    'ά': 'a', 'έ': 'e', 'ή': 'i', 'ί': 'i', 'ό': 'o', 'ύ': 'y', 'ώ': 'o',
    'ϊ': 'i', 'ϋ': 'y', 'ΐ': 'i', 'ΰ': 'y',
}

# Smart punctuation → ASCII equivalents
_SMART_PUNCT: dict = {
    '…': '...',   # … ellipsis
    '–': '-',     # – en-dash
    '—': ' - ',   # — em-dash
    '‘': "'",     # ' left single quotation
    '’': "'",     # ' right single quotation
    '“': '"',     # " left double quotation
    '”': '"',     # " right double quotation
    '«': '"',     # « left-pointing double angle quotation
    '»': '"',     # » right-pointing double angle quotation
    '‐': '-',     # ‐ hyphen
    '‑': '-',     # ‑ non-breaking hyphen
    '‒': '-',     # ‒ figure dash
    '―': '-',     # ― horizontal bar
    ' ': ' ',     # NBSP → space
    ' ': ' ',     # narrow NBSP → space
    ' ': ' ',     # thin space → space
    '​': '',      # zero-width space → remove
    '‌': '',      # zero-width non-joiner → remove
    '‍': '',      # zero-width joiner → remove
    '﻿': '',      # BOM → remove
}

# Better substitutions for Windows-illegal chars
_FILENAME_CHAR_REPL: dict = {
    ':':  ' -',
    '"':  "'",
    '|':  '-',
    '?':  '',
    '*':  '',
    '<':  '(',
    '>':  ')',
    '\\': '-',
    '/':  '-',
}

_MULTI_SPACE_RE = re.compile(r'\s{2,}')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_INITIALISM_RE = re.compile(r'(?:^|[\s(\[])(?:[A-Za-z0-9]\.){2,}$')


def transliterate_ascii(s):
    """Any text to plain ASCII: Cyrillic, Greek, diacritics, non-decomposing letters, smart
    punctuation; anything left is dropped (a stable placeholder if that would leave nothing)."""
    if not s:
        return s
    out = ''.join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in s)
    out = ''.join(_GREEK_TO_LATIN.get(ch, ch) for ch in out)
    out = _ud.normalize('NFKD', out)
    out = ''.join(c for c in out if not _ud.combining(c))
    out = out.translate(_ARTIST_TRANSLIT)
    out = out.translate(_SYMBOL_TRANSLIT)
    for uni, asc in _SMART_PUNCT.items():
        out = out.replace(uni, asc)
    if any(ord(c) > 127 for c in out):
        folded = ''.join(c for c in out if ord(c) < 128)
        if folded.strip():
            out = folded
        else:
            out = 'u' + hashlib.sha1(out.encode('utf-8', errors='replace')).hexdigest()[:8]
    return out


def clean(s, keep_trailing=False):
    """One name part: ASCII, Windows-safe replacements, no control characters, single spaces.
    Trailing dots and spaces are dropped (a folder cannot end in them, and the library drops them
    from artist and album names too) -- except with keep_trailing (a track title, which the
    extension follows): there a trailing ellipsis or initialism ("A.I.R.") stays."""
    if not s:
        return s
    out = transliterate_ascii(s)
    out = ''.join(_FILENAME_CHAR_REPL.get(ch, ch) for ch in out)
    out = _CONTROL_RE.sub(' ', out)
    out = _MULTI_SPACE_RE.sub(' ', out).strip()
    if keep_trailing:
        while out and out[-1] in ' .':
            if out[-1] == '.' and (re.search(r'\.{2,}$', out) or _INITIALISM_RE.search(out)):
                break
            out = out[:-1]
        return out
    return out.rstrip(' .')

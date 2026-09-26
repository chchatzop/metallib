# MetalLib -- the library's pressing tags, from the chosen pressing (user, 2026-09-26).
#
# The library keeps what sets a pressing apart in tags, and the naming script turns them into the
# folder name (survey of 6,814 albums in Z:\1 Metal):
#   edition   Jap / Lim / US / Dlx / Exp Del / Expnd (several: "Jap; Lim")  -> (Jap. Ed.) (Lim. Ed.) ...
#   remaster  the year of a remastered pressing                             -> (RM yyyy)
#   reissue   the year of a later pressing                                  -> (RE yyyy)
# The same rules as the naming script (metallib_naming/metallib_layout.txt), from the pressing's
# notes ("Remastered, limited edition", packaging, media), its year and country. Pure logic.
#
# SPDX-License-Identifier: GPL-2.0-or-later

RE_GAP = 2          # a pressing this many years after the first release is a reissue (naming _re_gap)

EDITION_ORDER = ('Jap', 'Lim', 'US', 'Dlx', 'Exp Del', 'Expnd')


def _year(value):
    value = (value or '')[:4]
    return int(value) if value.isdigit() else None


def derive(md):
    """-> {'edition': [...], 'remaster': 'yyyy', 'reissue': 'yyyy'} (only what applies) for a
    Metadata holding the chosen pressing's values."""
    notes = ' '.join(md.getall('~releasecomment') + md.getall('~releasepackaging') + md.getall('media')).lower()
    first = _year(md['originalyear'] or md['originaldate'])
    pressing = _year(md['date'])
    later = pressing if first and pressing and pressing - first >= RE_GAP else None
    out = {}
    editions = set()
    if 'japan' in notes or (md['~pressingcountry'] == 'JP' and md['releasecountry'] != 'JP'):
        editions.add('Jap')
    if 'limited' in notes:
        editions.add('Lim')
    if 'deluxe' in notes:
        editions.add('Dlx')
    if 'expanded' in notes:
        editions.add('Expnd')
    if editions:
        out['edition'] = [e for e in EDITION_ORDER if e in editions]
    if later and 'remaster' in notes:
        out['remaster'] = str(later)
    elif later:
        out['reissue'] = str(later)
    return out


TAGS = ('edition', 'remaster', 'reissue')


# -- the media tag, the library's words (user, 2026-09-27) ----------------------------------------
# "Digital Media", "CD", "Vinyl", "Cassette"; a CD in a digipak also gets "Digipak" as a second value
# ("CD; Digipak", 44 of 1,500 sampled library albums). Scene values like "WEB/FLAC 16bit" or
# Discogs' "CD, Album, Digipak" are turned into those; an unrecognised one (DVD) is left as it is.

MEDIA_WORDS = {'Digital': 'Digital Media', 'CD': 'CD', 'Vinyl': 'Vinyl', 'Cassette': 'Cassette'}


def media_values(values, notes='', fallback=''):
    """-> the media tag values the library way, or None to leave them (nothing recognisable)."""
    from .ma_release import format_kind
    values = [v for v in values if v]
    kind = next((format_kind(v) for v in values if format_kind(v)), '') or format_kind(fallback)
    if not kind:
        return None
    out = [MEDIA_WORDS[kind]]
    text = ' '.join(values + [notes]).lower()
    if kind == 'CD' and ('digipak' in text or 'digipack' in text):
        out.append('Digipak')
    return out

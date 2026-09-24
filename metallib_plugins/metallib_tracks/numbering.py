# MetalLib tracks -- track numbers of an album that was NOT looked up (a cluster), ported from the
# user's "Tag & Rename" tool (tag_reader.normalize_track_numbers / track_number_problems).
#
# A finished album must be numbered 1..N per disc with nothing missing. Only an OFFSET but otherwise
# complete run is fixed: 8 files tagged 02..09 (a ripper counted the cover as 01) become 01..08.
# Gaps or duplicates mean a partial rip or a real numbering problem: those keep their numbers
# (a folder missing track 4 stays 5 -> 5, never 4) and are reported instead. Numbers come from the
# tags only, never from filenames (user rule).
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re


def number(value):
    """ "3", "03", "3/9" -> 3; anything else -> 0."""
    m = re.match(r'\s*(\d+)', str(value or ''))
    return int(m.group(1)) if m else 0


def plan(items):
    """items: [{'disc': int, 'number': int, 'total': int}] (0 = unknown). Returns (new_numbers, notes):
    new_numbers {index: new number} for files of a shifted disc; notes {disc: (kind, text)} with
    kind 'shifted' or 'problem' -- per disc, so each file can say what happened to its disc."""
    by_disc = {}
    for i, it in enumerate(items):
        by_disc.setdefault(it.get('disc') or 1, []).append(i)
    multi = len(by_disc) > 1
    new, notes = {}, {}
    for disc in sorted(by_disc):
        idx = by_disc[disc]
        nums = [items[i]['number'] for i in idx]
        where = ('disc %d: ' % disc) if multi else ''
        if any(n <= 0 for n in nums):
            notes[disc] = ('problem', '%s%d file(s) have no track number' % (where, sum(1 for n in nums if n <= 0)))
            continue
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        if dupes:
            notes[disc] = ('problem', '%strack number%s %s used more than once'
                           % (where, 's' if len(dupes) > 1 else '', ', '.join(str(d) for d in dupes)))
            continue
        lo, hi = min(nums), max(nums)
        if hi - lo + 1 != len(nums):
            missing = sorted(set(range(lo, hi + 1)) - set(nums))
            notes[disc] = ('problem', '%smissing track number%s %s (numbers run %d..%d)'
                           % (where, 's' if len(missing) > 1 else '',
                              ', '.join(str(m) for m in missing[:8]) + ('...' if len(missing) > 8 else ''), lo, hi))
            continue
        if lo == 1:
            continue                                   # already 1..N
        # 03..05 may be an offset OR a rip missing tracks 1-2: undecidable -> flag, never guess
        # (user rule). A total-tracks tag decides when the files have one (02..09 with total 9 is
        # a rip missing track 1; with total 8 an offset); without it only a run starting at 02 is
        # taken as an offset (a ripper counted the cover as 01).
        totals = {items[i].get('total') or 0 for i in idx} - {0}
        offset = totals == {len(nums)} if totals else lo == 2
        if not offset:
            first = 'track 1' if lo == 2 else 'tracks 1-%d' % (lo - 1)
            notes[disc] = ('problem', '%snumbers run %d..%d - %s missing, or an offset?' % (where, lo, hi, first))
            continue
        for i in idx:
            new[i] = items[i]['number'] - lo + 1
        notes[disc] = ('shifted', '%stracks %02d-%02d renumbered to 01-%02d (none missing)'
                       % (where, lo, hi, len(nums)))
    return new, notes

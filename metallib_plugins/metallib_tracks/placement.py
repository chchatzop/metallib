# MetalLib track placement -- decide which file is which track by TITLE + DURATION.
#
# User rules (2026-09-21/23):
#   * Track position never comes from the filename (and not from position/order alone).
#   * Match each file to the source tracklist by title + duration.
#   * Undecidable -> refuse and flag (leave it unmatched; never guess).
#   * A confident title+duration match overrides a disagreeing track-number tag, but is reported
#     as "renumbered".
# Background: a past bug renamed files positionally, so titles landed on the wrong audio. The
# duration is the one thing a bad earlier retag cannot have corrupted, so a title that points at a
# track whose length contradicts the audio is a conflict, not a match.
#
# Pure logic, no Picard imports except the title similarity function passed in by the caller.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re
import unicodedata


DUR_OK_S = 5        # |file - source| within this: durations agree (dupes.js realign TOL)
DUR_BAD_S = 10      # beyond this: durations contradict (dupes.js red / confirm-dialog threshold)
TITLE_STRONG = 0.8  # title similarity that identifies a track on its own
TITLE_NEAR = 0.15   # tracks within this of the best title score are "the same by title"
TITLE_WEAK = 0.5    # a real title scoring below this against every track names none of them

# Titles that carry no information: "", "Track 01", "Untitled", "Audio Track 3", "05".
_JUNK_TITLE_RE = re.compile(r'^\s*(?:(?:audio\s+)?track|piste|pista|titel|untitled|unknown|'
                            r'no\s+title|sans\s+titre)?\s*[-#._]?\s*\d*\s*$', re.IGNORECASE)

OK = 'ok'
RENUMBERED = 'renumbered'
UNPLACED = 'unplaced'
ASSUMED = 'assumed'     # the only free track left with a matching length: placed, but flagged

_BRACKETS_RE = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]')


_DOTTED_RE = re.compile(r'(?<![A-Za-z0-9])((?:[A-Za-z0-9]\.){2,}[A-Za-z0-9]?)\.?(?![A-Za-z0-9])')


def undot(title):
    """Dotted acronyms as plain words: "N.Y.C. 93" -> "NYC 93", "T.O.M.B" -> "TOMB"."""
    return _DOTTED_RE.sub(lambda m: m.group(1).replace('.', ''), title or '')


def _fold(title):
    s = unicodedata.normalize('NFKD', undot(title))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.replace('ø', 'o').replace('Ø', 'O').replace('æ', 'ae').replace('ß', 'ss').strip()


def _initials(title):
    """"Target on My Back" -> "tomb" (letters/digits of each word's first character)."""
    return ''.join(w[0] for w in re.findall(r"[A-Za-z0-9]+", title or '')).lower()


def _acronym(title):
    """"T.O.M.B." / "T.O.M.B" / "TOMB" -> "tomb"; None when the title is not written as an acronym
    (dotted letters, or one all-caps word of 2-8 letters)."""
    t = (title or '').strip()
    if re.fullmatch(r'(?:[A-Za-z0-9]\.){2,}[A-Za-z0-9]?\.?', t):
        return re.sub(r'[^A-Za-z0-9]', '', t).lower()
    if re.fullmatch(r'[A-Z0-9]{2,8}', t):
        return t.lower()
    return None


def is_initialism_of(short, long):
    """True when `short` is written as an acronym of `long`'s words: "T.O.M.B." ~ "Target on My Back"."""
    acro = _acronym(short)
    return bool(acro) and len(acro) >= 3 and len(re.findall(r"[A-Za-z0-9]+", long or '')) == len(acro) \
        and _initials(long) == acro


def title_similarity(similarity, a, b, raw=False):
    """Best of the raw and the bracket-stripped comparison: "(Bonus Track)", "(Remastered)"
    and similar suffixes must not hide an otherwise identical title. raw=True compares the full
    titles only (used to break ties such as "Incarnate" vs "Incarnate (Radio Edit)")."""
    a, b = _fold(a), _fold(b)
    if not a or not b:
        return 0.0
    if is_initialism_of(a, b) or is_initialism_of(b, a):
        return 1.0                  # Anthrax "T.O.M.B." is "Target on My Back"; the length still has to agree
    best = similarity(a, b)
    if raw:
        return best
    a2, b2 = _BRACKETS_RE.sub('', a).strip(), _BRACKETS_RE.sub('', b).strip()
    if a2 and b2 and (a2, b2) != (a, b):
        best = max(best, similarity(a2, b2))
    return best


def _label(t):
    return t.get('label') or str(t.get('number') or '?')


def _fmt_len(ms):
    s = int(round(ms / 1000))
    return '%d:%02d' % (s // 60, s % 60)


def _decide(f, tracks, similarity, taken=frozenset()):
    """-> (track index, how) or (None, reason). `f`/`tracks` items: title, length (ms, 0=unknown).
    Tracks in `taken` are already held by a sure match and are not candidates."""
    flen = f.get('length') or 0
    free = [i for i in range(len(tracks)) if i not in taken]

    def delta(t):
        tlen = t.get('length') or 0
        return abs(flen - tlen) / 1000.0 if flen and tlen else None

    def dur_bad(t):
        d = delta(t)
        return d is not None and d > DUR_BAD_S

    # The audio fingerprint (AcoustID -> MusicBrainz recordings) is the strongest evidence there is:
    # it is the audio itself, so even a file whose tags were swapped by an earlier bad retag lands
    # right. It only decides when it points at exactly one track and the length agrees.
    fp = set(f.get('recording_ids') or ())
    if fp:
        hits = [i for i in range(len(tracks)) if fp & set(tracks[i].get('recording_ids') or ())]
        if len(hits) == 1:
            i = hits[0]
            if i in taken:
                return None, 'fingerprint matches track %s "%s", which already has a file' % (
                    _label(tracks[i]), tracks[i].get('title'))
            if dur_bad(tracks[i]):
                return None, 'fingerprint matches track %s "%s" but the audio is %s vs %s' % (
                    _label(tracks[i]), tracks[i].get('title'), _fmt_len(flen),
                    _fmt_len(tracks[i].get('length') or 0))
            return i, 'fingerprint'

    sims = [title_similarity(similarity, f.get('title'), t.get('title')) for t in tracks]
    best = max(sims, default=0.0)

    if best >= TITLE_STRONG:
        # Judge the title against ALL tracks: a title that names an already-placed track (a
        # second copy of a song) must not fall through to some other free track by duration.
        by_title = [i for i in range(len(tracks)) if sims[i] >= best - TITLE_NEAR]
        if not any(i in free for i in by_title):
            return None, 'title matches track %s "%s", which already has a file' % (
                _label(tracks[by_title[0]]), tracks[by_title[0]].get('title'))
        by_title = [i for i in by_title if i in free]
        if len(by_title) > 1:
            # Tied only because brackets were ignored? The full title decides: "Incarnate
            # (Radio Edit)" belongs to the radio-edit track, plain "Incarnate" to the other.
            raw = {i: title_similarity(similarity, f.get('title'), tracks[i].get('title'), raw=True)
                   for i in by_title}
            top = max(raw.values())
            exact = [i for i in by_title if raw[i] >= top - 0.01]
            if len(exact) == 1 and all(raw[i] < top - TITLE_NEAR for i in by_title if i not in exact):
                by_title = exact
        fits = [i for i in by_title if not dur_bad(tracks[i])]
        if not fits:
            i = max(by_title, key=lambda i: sims[i])
            return None, ('title matches track %s "%s" but the audio is %s vs %s'
                          % (_label(tracks[i]), tracks[i].get('title'), _fmt_len(flen),
                             _fmt_len(tracks[i].get('length') or 0)))
        if len(fits) == 1:
            return fits[0], 'title+duration' if delta(tracks[fits[0]]) is not None else 'title'
        # Several tracks share the title (Part I/II, reprises): only a clear duration fit decides.
        close = [i for i in fits if delta(tracks[i]) is not None and delta(tracks[i]) <= DUR_OK_S]
        if len(close) == 1:
            return close[0], 'title+duration'
        return None, 'title fits tracks %s equally and duration does not decide' % ', '.join(
            _label(tracks[i]) for i in fits)

    # A real title that names none of the tracks is evidence the file is not on this release
    # (King Winter on Mortlach's album): refuse it rather than place it by length.
    title = _fold(f.get('title'))
    if title and not _JUNK_TITLE_RE.match(title) and best < TITLE_WEAK:
        return None, 'title "%s" matches no track of this release' % f.get('title')

    # No usable title (missing or "Track 01"): duration alone, and only when it is unmistakable --
    # exactly one track within DUR_OK_S and no other within DUR_BAD_S.
    if not flen:
        return None, 'no title match and no duration to go by'
    deltas = [(delta(tracks[i]), i) for i in free if delta(tracks[i]) is not None]
    if not deltas:
        return None, 'no title match and the source has no track lengths'
    close = [i for d, i in deltas if d <= DUR_OK_S]
    near = [i for d, i in deltas if d <= DUR_BAD_S]
    if len(close) == 1 and len(near) == 1:
        # A real title must at least roughly agree with the track its length points to; a length
        # coincidence alone ("Executed on Site" 3:46 vs "We Are the Only Ones" 3:43) is not a match.
        if title and not _JUNK_TITLE_RE.match(title) and sims[close[0]] < TITLE_WEAK:
            return None, 'title "%s" does not match track %s "%s" of similar length' % (
                f.get('title'), _label(tracks[close[0]]), tracks[close[0]].get('title'))
        return close[0], 'duration'
    if not close:
        return None, 'no title match and no track has a similar length (%s)' % _fmt_len(flen)
    return None, 'no title match and several tracks have a similar length (%s)' % ', '.join(
        _label(tracks[i]) for i in near)


def place(files, tracks, similarity):
    """Place files onto tracks, one-to-one.

    files:  [{title, length (ms), tracknumber (the file's own TAG, '' if none),
              recording_ids (MusicBrainz recordings its audio fingerprint matched, optional)}]
    tracks: [{title, length (ms), number (track number on its disc), label (e.g. "2-03"),
              recording_ids (the track's MusicBrainz recording ids, optional)}]
    Returns one result per file: {'track': index or None, 'status': OK/RENUMBERED/UNPLACED,
    'reason': str}. Two files claiming the same track are BOTH unplaced -- never pick one.
    """
    # Rounds: accept every sure, uncontested match, take those tracks off the table, and decide
    # the rest again -- a file torn between tracks 4 and 9 is sure once 9 is held by its real
    # owner. Only uncontested picks are ever accepted, so this never turns a tie into a guess.
    final = {}                  # file index -> (track index, how)
    reasons = {}                # file index -> why it is unplaced (last round's verdict)
    blocked = set()             # tracks claimed by several files: nobody gets them
    while True:
        taken = {ti for ti, _ in final.values()} | blocked
        picks = {fi: _decide(files[fi], tracks, similarity, taken)
                 for fi in range(len(files)) if fi not in final}
        claims = {}
        for fi, (ti, _) in picks.items():
            if ti is not None:
                claims.setdefault(ti, []).append(fi)
        progress = False
        for fi, (ti, how) in picks.items():
            if ti is None:
                reasons[fi] = how
            elif len(claims[ti]) > 1:
                reasons[fi] = '%d files match track %s "%s"' % (
                    len(claims[ti]), _label(tracks[ti]), tracks[ti].get('title'))
                blocked.add(ti)
            else:
                final[fi] = (ti, how)
                progress = True
        if not progress:
            break

    # Last free tracks (user rule 2026-09-23): "1 empty spot and 1 file unmatched with the same duration
    # -> match it even if the name is wrong, and flag it". Once at least half of the album's files are
    # placed by title the release is confirmed; then leftover files that fit EXACTLY ONE free track by
    # length (within DUR_OK_S, one-to-one) go there, flagged ASSUMED (Picard's match colour shows the
    # title mismatch). Anything ambiguous stays unplaced.
    assumed = {}
    if final and len(final) * 2 >= len(files):
        taken = {ti for ti, _ in final.values()}
        free = [i for i in range(len(tracks)) if i not in taken and i not in blocked]
        left = [fi for fi in range(len(files)) if fi not in final]
        fits = {}
        for fi in left:
            flen = files[fi].get('length') or 0
            fits[fi] = [ti for ti in free if flen and tracks[ti].get('length')
                        and abs(flen - tracks[ti]['length']) / 1000.0 <= DUR_OK_S]
        picks = [fits[fi][0] for fi in left if len(fits[fi]) == 1]
        for fi in left:
            if len(fits[fi]) == 1 and picks.count(fits[fi][0]) == 1:
                assumed[fi] = fits[fi][0]

    out = []
    for fi in range(len(files)):
        if fi in assumed:
            ti = assumed[fi]
            out.append({'track': ti, 'status': ASSUMED,
                        'reason': 'the only free track with this length (%s "%s"); the title "%s" does not match'
                        % (_label(tracks[ti]), tracks[ti].get('title'), files[fi].get('title'))})
            continue
        if fi not in final:
            out.append({'track': None, 'status': UNPLACED, 'reason': reasons.get(fi, '')})
            continue
        ti, how = final[fi]
        tag = str(files[fi].get('tracknumber') or '').split('/')[0].strip()
        number = str(tracks[ti].get('number') or '')
        if tag and tag.lstrip('0') != number.lstrip('0'):
            out.append({'track': ti, 'status': RENUMBERED,
                        'reason': 'track number tag said %s, %s says %s' % (tag, how, number)})
        else:
            out.append({'track': ti, 'status': OK, 'reason': how})
    return out

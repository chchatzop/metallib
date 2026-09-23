# MetalLib quality labels -- pure logic, no Picard imports (unit-testable on its own).
#
# Ported from the user's "Tag & Rename" tool (tag_reader._lame_vbr_tier / _picard_format_tag,
# mixed_audit.quality_label / group_by_quality). Labels match the folder-name suffixes already
# used in the sorted library: 16-44, 24-96, V0, V2, 320K, 192K, VBR.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import re


_LAME_V_RE = re.compile(r'(?:^|\s)-V\s*(\d)', re.IGNORECASE)
# '--alt-preset standard' IS -V 2; 'insane' and '--preset 320' are CBR 320, not a V tier.
_LAME_PRESET_RE = re.compile(r'--(?:alt-)?preset\s+(?:fast\s+)?(standard|extreme|insane)', re.IGNORECASE)
_LAME_P320_RE = re.compile(r'--(?:alt-)?preset\s+320\b', re.IGNORECASE)
_LAME_ALIAS_TIER = {'standard': 'V2', 'extreme': 'V0', 'insane': '320K'}

# Nominal average kbps per V tier, used ONLY to reject a header the audio flatly contradicts
# (e.g. '-V 6' recorded alongside a -b 320 floor: every track 319 kbps). Genuine rips measured
# 0.66x-1.44x of nominal on 118 library files; the window is deliberately wider than that.
_LAME_TIER_NOMINAL = {'V0': 245, 'V1': 225, 'V2': 190, 'V3': 175, 'V4': 165,
                      'V5': 130, 'V6': 115, 'V7': 100, 'V8': 85, 'V9': 65}
_LAME_TIER_LO, _LAME_TIER_HI = 0.55, 1.75

# Every legal MPEG Layer III CBR bitrate (MPEG-1 32..320, MPEG-2/2.5 adds 8/16/24/144).
MP3_CBR_BITRATES = (320, 256, 224, 192, 160, 144, 128, 112, 96, 80, 64, 56, 48, 40, 32, 24, 16, 8)

_CBR_TOLERANCE_KBPS = 2     # CBR tracks share one bitrate; more spread than this is VBR
_CLUSTER_GAP_KBPS = 32      # a jump this large between sorted bitrates separates two qualities

MIXED = 'Mixed'


def lame_tier(encoder_settings, bitrate_kbps=0):
    """The preset LAME RECORDED in its header, as 'V0'..'V9' or '320K', else ''.

    Never a bitrate-band guess: guessing the tier from average bitrate disagreed with the real
    header 42% of the time (LAME 3.97 --vbr-new runs hot; genuine -V 2 averages 219-241 kbps).
    An honest VBR beats a confident wrong V1.
    """
    s = (encoder_settings or '').strip()
    if not s:
        return ''
    tier = ''
    m = _LAME_V_RE.search(s)
    if m:
        tier = 'V' + m.group(1)
    else:
        m = _LAME_PRESET_RE.search(s)
        if m:
            tier = _LAME_ALIAS_TIER[m.group(1).lower()]
        elif _LAME_P320_RE.search(s):
            tier = '320K'
    if not tier:
        return ''               # ABR, non-LAME encoders, nothing recorded
    nominal = _LAME_TIER_NOMINAL.get(tier)
    if nominal and bitrate_kbps:
        ratio = float(bitrate_kbps) / nominal
        if ratio < _LAME_TIER_LO or ratio > _LAME_TIER_HI:
            return ''
    return tier


def _snap_cbr(kbps):
    for exact in MP3_CBR_BITRATES:
        if abs(kbps - exact) < _CBR_TOLERANCE_KBPS:
            return '%dK' % exact
    return ''


def file_label(q):
    """One file's quality label from a dict with keys:
    lossless (bool), bits, sample_rate, kbps, vbr (encoder declared VBR/ABR), encoder_settings,
    mp3 (bool). Returns '' when the format has no label (e.g. Ogg/Opus/AAC).
    """
    if not q:
        return ''
    if q.get('lossless'):
        bits, sr = q.get('bits') or 0, q.get('sample_rate') or 0
        return '%d-%d' % (bits, sr // 1000) if bits and sr else ''
    if not q.get('mp3'):
        return ''
    kbps = q.get('kbps') or 0
    if not kbps:
        return ''
    # What the encoder recorded beats the CBR snap: a -V 2 file averaging 192 is V2, not 192K.
    tier = lame_tier(q.get('encoder_settings'), kbps)
    if tier:
        return tier
    if q.get('vbr'):
        return 'VBR'
    return _snap_cbr(kbps) or 'VBR'


def group_labels(files):
    """Group per-file quality dicts into {label: [index into files, ...]}, judging lossy files as a SET.

    Per-track average bitrate varying is the definition of VBR, so header-less MP3s (most scene
    rips: no Xing/LAME header) are clustered by bitrate gap: one continuous run is one VBR encode,
    even if one track happens to average 193 and would snap to 192K on its own.
    """
    groups = {}

    def add(label, i):
        if label:
            groups.setdefault(label, []).append(i)

    lossy = []
    for i, q in enumerate(files):
        if not q:
            continue
        if q.get('mp3') and not q.get('lossless'):
            lossy.append((i, q))
        else:
            add(file_label(q), i)

    ks = sorted({q.get('kbps') or 0 for _, q in lossy if (q.get('kbps') or 0) > 0})
    any_vbr = any(q.get('vbr') for _, q in lossy)

    # Genuine CBR set (every rate sits exactly on a legal CBR value, none declared VBR): each file
    # is its own bitrate, never merged -- 32/160/128 CBR is mixed, not one VBR album.
    if lossy and not any_vbr and len(ks) > 1 and all(k in MP3_CBR_BITRATES for k in ks):
        for i, q in lossy:
            add(file_label(q), i)
        return groups

    clusters = []
    for k in ks:
        if clusters and k - clusters[-1][-1] <= _CLUSTER_GAP_KBPS:
            clusters[-1].append(k)
        else:
            clusters.append([k])

    def cluster_label(kbps):
        for c in clusters:
            if c[0] <= kbps <= c[-1]:
                if c[-1] - c[0] <= _CBR_TOLERANCE_KBPS and not any_vbr:
                    return _snap_cbr(kbps) or 'VBR'
                return 'VBR'
        return ''

    for i, q in lossy:
        # A recorded LAME preset is per-file ground truth and bypasses the clustering, so a folder
        # mixing -V 0 and -V 2 correctly lands in two groups.
        label = file_label(q) if q.get('encoder_settings') else ''
        if not re.match(r'^(V\d|320K)$', label):
            label = cluster_label(q.get('kbps') or 0)
        add(label, i)
    return groups


def album_label(files):
    """The single label every file agrees on, MIXED when they disagree, '' when none is known."""
    groups = group_labels(files)
    if not groups:
        return ''
    if len(groups) == 1:
        return next(iter(groups))
    return MIXED

# MetalLib spectral check -- fake-lossless / upscaled-lossy / upsampled-hi-res detection.
#
# Ported from the user's Tag & Rename tool (app._spectral_* / _hires_*), keeping its calibration
# and its hard-won lessons:
#   * Measure with an FFT brick-wall (ffmpeg firequalizer) and read the MEAN energy. The old 2-pole
#     biquad highpass leaked loud sub-cutoff content into every band (a 16 kHz FLAC read 20 kHz),
#     and max_volume is a single-sample ringing artefact (~-48 dB even on an empty band).
#   * A band is "empty" relative to the FILE'S OWN floor (6 dB), never a fixed dB level.
#   * Depth guard: a quiet-but-full-band recording (Bell Witch) is not band-limited -- only a floor
#     >= 55 dB below a 2 kHz reference is digital silence.
#   * A gradual roll-off below 21 kHz is a dark master, not a transcode; only a sharp cliff
#     (>= 16 dB in one 0.5 kHz step) accuses. (Fixed here: the original only ran this shape probe
#     for 20-21 kHz, so a dark master under 20 kHz was still accused -- Aberrant Extermination.)
#   * Hi-res upsample: the cumulative profile 20.5 -> 26 kHz SLIDES, it never steps. Upsampled when
#     it collapses >= 20 dB or sits at the digital-silence floor (<= -85 dB); genuine when flat
#     (<= 8 dB) and alive (>= -75 dB); otherwise undecided -- never guessed.
#   * Sample real songs: skip files under 600 KB (pregap "[silence].flac" tracks, micro-intros),
#     take the largest songs plus a middle one, analyse 30 s from 35 % in (not the intro).
#   * ADVISORY ONLY. Nothing here changes a file; errs toward missing a fake rather than
#     accusing a genuine rip.
#
# Needs ffmpeg (on PATH). No Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import re
import shutil
import subprocess


LOSSLESS_EXTS = {'.flac', '.wav', '.ape', '.wv', '.tta', '.dsf', '.dff', '.alac', '.aiff', '.aif'}
LOSSY_EXTS = {'.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wma', '.mpc', '.mp2'}

EDGE_LADDER = [15000, 16000, 17000, 18000, 19000, 20000, 20500, 21000, 21500, 22000]
EDGE_TOL_DB = 6.0               # a band is empty within this of the file's own minimum (calibrated 3/6/8 -> 6)
SILENCE_DEPTH_DB = 55.0         # floor this far below the 2 kHz reference = digital silence (quiet != limited)
FINE_LADDER = [18000, 18500, 19000, 19500, 20000, 20500, 21000, 21500, 22000]
BRICKWALL_DROP_DB = 16.0        # one 0.5 kHz step dropping this much = a cliff (genuine 1.8-15.5, 320k upscale 16.5)
HIRES_ULTRASONIC_CUTOFF = 25000
HIRES_ULTRASONIC_GENUINE = -28.0
HIRES_LADDER = [20500, 21000, 21500, 22000, 22500, 23000, 23500, 24000, 24500, 25000, 26000]
HIRES_SPAN_UPSAMPLE = 20.0
HIRES_SPAN_GENUINE = 8.0
HIRES_TOP_FLOOR = -85.0
HIRES_TOP_GENUINE = -75.0
SONG_MIN_BYTES = 600_000
QUALITY_TIERS = ['≤128', '128–160', '192', '256', '320 / V0']

_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0      # CREATE_NO_WINDOW: no console popping up


def find_ffmpeg():
    return shutil.which('ffmpeg')


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          creationflags=_NO_WINDOW)


# -- measurements -----------------------------------------------------------------------------------

def probe_window(ffmpeg, path):
    """(ss, win): 30 s from 35 % into the track (past quiet intros), or the whole file if short."""
    dur = 0.0
    try:
        err = _run([ffmpeg, '-i', path], timeout=20).stderr.decode('utf-8', 'replace')
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', err)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    if dur > 120:
        return round(dur * 0.35, 1), 30
    return 0, int(dur) or 30


def mean_above(ffmpeg, path, lo, ss, win):
    """Mean energy (dB) ABOVE `lo` Hz through an FFT brick-wall. float | 'transient' | None."""
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-ss', str(ss), '-i', path, '-t', str(win),
           '-af', "firequalizer=gain='if(lt(f,%d),-200,0)',volumedetect" % lo, '-f', 'null', '-']
    try:
        err = _run(cmd).stderr.decode('utf-8', 'replace')
    except Exception:
        return 'transient'
    m = re.search(r'mean_volume:\s*([-\d.]+)\s*dB', err)
    return float(m.group(1)) if m else None


def effective_bandwidth(ffmpeg, path, window=None):
    """(kHz | None, transient). The lowest rung whose band has fallen to the file's own floor;
    22.05 = content past the top rung (full bandwidth) or merely quiet up there (depth guard)."""
    ss, win = window or probe_window(ffmpeg, path)
    means, transient = [], False
    for lo in EDGE_LADDER:
        res = mean_above(ffmpeg, path, lo, ss, win)
        if res == 'transient':
            transient = True
            break
        if res is None:
            break
        means.append((lo, res))
    if not means:
        return None, transient
    floor = min(m for _, m in means)
    ref = mean_above(ffmpeg, path, 2000, ss, win)
    if isinstance(ref, float) and (ref - floor) < SILENCE_DEPTH_DB:
        return 22.05, transient
    for lo, m in means:
        if m <= floor + EDGE_TOL_DB:
            return ((lo / 1000.0) - 2.0 if lo == EDGE_LADDER[0] else lo / 1000.0), transient
    return 22.05, transient


def shape_ladder(edge_khz=None):
    """0.5 kHz rungs BRACKETING the measured edge (2 kHz below .. 1.5 kHz above). A fixed 18-22 kHz
    ladder cannot see a cliff at 16 kHz -- a 128k transcode then looks "gradual" and would be excused
    as a dark master (found on synthetic ground truth)."""
    if not edge_khz:
        return FINE_LADDER
    lo = max(12000, int(round((edge_khz - 2.0) * 2)) * 500)
    hi = min(22000, int(round((edge_khz + 1.5) * 2)) * 500)
    return list(range(lo, hi + 1, 500))


def rolloff_shape(ffmpeg, path, window=None, edge_khz=None):
    """The SHAPE of the roll-off around the edge: {cutoff_khz, max_drop_db, sharp} or
    {'transient'|'insufficient': True}. A lossy encoder's low-pass is a cliff; a real master tapers."""
    ss, win = window or probe_window(ffmpeg, path)
    cuts, means = [], []
    for cut in shape_ladder(edge_khz):
        res = mean_above(ffmpeg, path, cut, ss, win)
        if res == 'transient':
            return {'transient': True}
        if res is None:
            break
        cuts.append(cut)
        means.append(res)
    if len(means) < 3:
        return {'insufficient': True}
    floor = min(means)
    death = next((i for i, m in enumerate(means) if m <= floor + EDGE_TOL_DB), None)
    max_drop = max((means[i - 1] - means[i] for i in range(1, len(means))), default=0.0)
    return {'cutoff_khz': round((cuts[death] if death is not None else cuts[-1]) / 1000.0, 1),
            'max_drop_db': round(max_drop, 1), 'sharp': max_drop >= BRICKWALL_DROP_DB}


def hires_profile(ffmpeg, path, window=None):
    """{verdict upsampled|genuine|undecided, span_db, top_db, floor} or None (unmeasurable)."""
    ss, win = window or probe_window(ffmpeg, path)
    steps = []
    for cut in HIRES_LADDER:
        v = mean_above(ffmpeg, path, cut, ss, win)
        if v == 'transient':
            return None
        if v is None:
            break
        steps.append(v)
    if len(steps) < 6:
        return None
    span, top = steps[0] - steps[-1], steps[-1]
    if span >= HIRES_SPAN_UPSAMPLE or top <= HIRES_TOP_FLOOR:
        verdict = 'upsampled'
    elif span <= HIRES_SPAN_GENUINE and top >= HIRES_TOP_GENUINE:
        verdict = 'genuine'
    else:
        verdict = 'undecided'
    return {'verdict': verdict, 'span_db': round(span, 1), 'top_db': round(top, 1),
            'floor': top <= HIRES_TOP_FLOOR}


def used_bits(ffmpeg, path, window=None):
    """Bits actually carrying data (astats Bit depth max): a 16->24 zero-pad reads 16."""
    ss, win = window or probe_window(ffmpeg, path)
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-ss', str(ss), '-i', path, '-t', str(win),
           '-af', 'astats=measure_overall=Bit_depth:measure_perchannel=Bit_depth', '-f', 'null', '-']
    try:
        err = _run(cmd).stderr.decode('utf-8', 'replace')
    except Exception:
        return None
    best = 0
    for m in re.finditer(r'Bit depth:\s*([\d/]+)', err):
        for part in m.group(1).split('/'):
            if part.isdigit():
                best = max(best, int(part))
    return best or None


# -- sampling ---------------------------------------------------------------------------------------

def sample_files(paths, max_files=3):
    """Representative SONGS: drop tiny files (< 600 KB: pregap silence, micro-intros -- they read
    like a transcode), then the largest, 2nd largest, a middle-position one, 3rd largest."""
    sized = []
    for p in paths:
        try:
            sized.append((os.path.getsize(p), p))
        except OSError:
            continue
    if not sized:
        return []
    songs = [(sz, p) for sz, p in sized if sz >= SONG_MIN_BYTES]
    pool = songs if len(songs) >= 2 else sized
    biggest = [p for _, p in sorted(pool, key=lambda x: -x[0])]
    by_pos = [p for _, p in pool]
    picks = [biggest[0]] + biggest[1:2] + [by_pos[len(by_pos) // 2]] + biggest[2:3]
    out = []
    for p in picks:
        if p not in out:
            out.append(p)
    return out[:max_files]


# -- verdicts ---------------------------------------------------------------------------------------

CLEAN, UNCERTAIN, SUSPICIOUS, TRANSCODE = 'clean', 'uncertain', 'suspicious', 'transcode'


def claimed_tier(kbps, is_vbr=False):
    """Highest tier a DECLARED lossy bitrate can support (VBR judged a tier up at the edges)."""
    if not kbps:
        return None
    if kbps >= (224 if is_vbr else 300):
        return '320 / V0'
    if kbps >= (176 if is_vbr else 224):
        return '256'
    if kbps >= (144 if is_vbr else 176):
        return '192'
    if kbps >= 124:
        return '128–160'
    return '≤128'


def spectral_tier(eff_khz):
    if eff_khz >= 20:
        return '320 / V0'
    if eff_khz >= 19.5:
        return '256'
    if eff_khz >= 18:
        return '192'
    if eff_khz >= 16.5:
        return '128–160'
    return '≤128'


def verdict(lossless, eff_khz, shape=None, claimed_kbps=None, is_vbr=False):
    """-> {verdict, label, confidence, explanation, tier}. `shape` = rolloff_shape() result."""
    tier = spectral_tier(eff_khz)
    e = '%.1f kHz' % eff_khz
    shape_ok = bool(shape and 'max_drop_db' in shape)
    if lossless:
        if eff_khz >= 21:
            return _v(CLEAN, '✓ Genuine lossless', 'high',
                      'Full-spectrum content up to ~%s, consistent with a genuine lossless rip.' % e, tier)
        if shape_ok and shape['sharp'] and eff_khz < 18:
            return _v(TRANSCODE, '✗ Likely fake lossless (transcode)', 'high',
                      'A hard cliff at ~%.1f kHz (%.0f dB in one 0.5 kHz step) with nothing above: the low-pass of '
                      'a ~%s kbps lossy encode, far below the ~21-22 kHz of genuine lossless.'
                      % (shape['cutoff_khz'], shape['max_drop_db'], tier), tier)
        if shape_ok and shape['sharp']:
            return _v(SUSPICIOUS, '⚠ Possible upscaled lossy (brick-wall cutoff)', 'medium',
                      'High frequencies stop abruptly at ~%.1f kHz (a %.0f dB cliff in one 0.5 kHz step) with '
                      'nothing above: the low-pass of a lossy encoder (~%s kbps) re-encoded to lossless. A genuine '
                      'rip tapers gradually. Confirm on a spectrogram.'
                      % (shape['cutoff_khz'], shape['max_drop_db'], tier), tier)
        if shape_ok:
            if eff_khz >= 20:
                return _v(CLEAN, '✓ Genuine lossless', 'high',
                          'Roll-off is gradual to ~%.1f kHz (steepest step only %.0f dB, no brick wall) - a '
                          'genuine rip, not an upscale.' % (shape['cutoff_khz'], shape['max_drop_db']), tier)
            return _v(UNCERTAIN, 'ⓘ Band-limited master (no brick wall)', 'low',
                      'Bandwidth stops at ~%s, below the ~21-22 kHz of a typical CD rip, but the roll-off is '
                      'GRADUAL (steepest step %.0f dB, no cliff). A lossy transcode cuts off abruptly at its '
                      'encoder low-pass, so this points to a dark / limited master rather than a transcode.'
                      % (e, shape['max_drop_db']), tier)
        if eff_khz >= 20:
            return _v(UNCERTAIN, '? Lossless or 320 kbps upscale', 'low',
                      'Bandwidth reaches ~%s: between a genuine rip (~21-22 kHz) and a 320 kbps upscale '
                      '(~20-20.5 kHz). Check a spectrogram for a hard wall.' % e, tier)
        if eff_khz >= 18:
            return _v(SUSPICIOUS, '⚠ Possibly fake lossless', 'medium',
                      'Bandwidth stops at ~%s; a genuine CD rip reaches ~21-22 kHz. Possibly transcoded from a '
                      '~%s kbps source.' % (e, tier), tier)
        return _v(TRANSCODE, '✗ Likely fake lossless (transcode)', 'high',
                  'Bandwidth stops at ~%s, far below genuine lossless: strong signature of a transcode from a '
                  '~%s kbps source.' % (e, tier), tier)
    # lossy: is it what it CLAIMS to be (not "is it good")
    labels = {'320 / V0': '✓ High quality (≈320 kbps / V0)', '256': '✓ Good quality (≈256 kbps)',
              '192': '○ Modest quality (≈192 kbps)', '128–160': '⚠ Low quality (≈128–160 kbps)',
              '≤128': '⚠ Very low quality (≤128 kbps)'}
    ceiling = claimed_tier(claimed_kbps, is_vbr)
    shown = ceiling if ceiling and QUALITY_TIERS.index(ceiling) < QUALITY_TIERS.index(tier) else tier
    label = labels[shown]
    if ceiling:
        gap = QUALITY_TIERS.index(ceiling) - QUALITY_TIERS.index(tier)
        v = CLEAN if gap <= 0 else (UNCERTAIN if gap == 1 else SUSPICIOUS)
        expl = ('Spectrum reaches ~%s, what a %d kbps encode looks like: it matches its declared bitrate.'
                % (e, claimed_kbps) if gap <= 0 else
                'Declares %d kbps but the spectrum only reaches ~%s (≈%s kbps).' % (claimed_kbps, e, tier))
        if gap >= 2:
            label = '⚠ Likely upscaled / re-encoded'
            expl += ' Likely re-encoded UP from a lower-bitrate source.'
    else:
        v = CLEAN if eff_khz >= 19 else (UNCERTAIN if eff_khz >= 18 else SUSPICIOUS)
        expl = 'Spectrum reaches ~%s, consistent with a ~%s kbps encode.' % (e, tier)
    return _v(v, label, 'high' if (eff_khz >= 20 or eff_khz < 16) else 'medium', expl, shown)


def hires_verdict(ultrasonic_db, profile, declared):
    """Hi-res (> 48 kHz) check: genuine | upsampled | uncertain, with a label."""
    if ultrasonic_db is not None and ultrasonic_db > HIRES_ULTRASONIC_GENUINE:
        return {'verdict': 'genuine', 'label': '✓ Genuine hi-res',
                'explanation': 'Real content above 25 kHz (%.1f dB): an upsample from 44.1/48 kHz is silent there.'
                % ultrasonic_db}
    pv = (profile or {}).get('verdict')
    if pv == 'upsampled':
        why = ('the band above sits at the digital-silence floor (%s dB)' % profile['top_db'] if profile['floor']
               else 'the level collapses %s dB across 20.5-26 kHz' % profile['span_db'])
        return {'verdict': 'upsampled', 'label': '✗ Likely upsampled',
                'explanation': 'Little energy above 25 kHz and %s. A genuine %s master stays roughly flat through '
                               '22-24 kHz; an upsample is empty above its source Nyquist. Confirm on a spectrogram.'
                               % (why, declared)}
    if pv == 'genuine':
        return {'verdict': 'genuine', 'label': '✓ Genuine hi-res (quiet master)',
                'explanation': 'Quiet above 25 kHz but the profile is FLAT (%s dB span, %s dB at the top): real '
                               'broadband content runs through 22-24 kHz.' % (profile['span_db'], profile['top_db'])}
    return {'verdict': 'uncertain', 'label': 'ⓘ Hi-res: check the spectrogram',
            'explanation': 'Little energy above 25 kHz: either a quiet / band-limited genuine %s master or an '
                           'upsample. An automated measurement cannot tell these apart.' % declared}


def _v(verdict_, label, confidence, explanation, tier):
    return {'verdict': verdict_, 'label': label, 'confidence': confidence, 'explanation': explanation,
            'tier': tier}


# -- album -------------------------------------------------------------------------------------------

def check_album(paths, claimed_kbps=None, is_vbr=False, sample_rate=0, bit_depth=0, ffmpeg=None):
    """Full check for one album's files. Returns a result dict (see keys) or {'ok': False, 'error'}."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return {'ok': False, 'error': 'ffmpeg not found on PATH'}
    audio = [p for p in paths if os.path.splitext(p)[1].lower() in LOSSLESS_EXTS | LOSSY_EXTS]
    samples = sample_files(audio)
    if not samples:
        return {'ok': False, 'error': 'no audio files'}
    lossless = sum(os.path.splitext(p)[1].lower() in LOSSLESS_EXTS for p in samples) * 2 >= len(samples)
    best, best_path, windows, transient = None, None, {}, False
    for p in samples:
        windows[p] = probe_window(ffmpeg, p)
        eff, tr = effective_bandwidth(ffmpeg, p, windows[p])
        transient |= tr
        if eff is not None and (best is None or eff > best):
            best, best_path = eff, p          # the album's DEMONSTRATED bandwidth: a fake cannot inflate it
    if best is None:
        return {'ok': False, 'error': 'ffmpeg timed out' if transient else 'could not decode the audio'}
    shape = None
    if lossless and best < 21:
        s = rolloff_shape(ffmpeg, best_path, windows[best_path], edge_khz=best)
        if 'max_drop_db' in s:
            shape = s
    out = dict(verdict(lossless, best, shape, claimed_kbps, is_vbr), ok=True, lossless=lossless,
               eff_khz=round(best, 1), shape=shape, files_checked=len(samples))
    if lossless and sample_rate > 54000:
        ultra = None
        for p in samples:
            u = mean_above(ffmpeg, p, HIRES_ULTRASONIC_CUTOFF, *windows[p])
            if isinstance(u, float) and (ultra is None or u > ultra):
                ultra = u
        profile = None if (ultra is not None and ultra > HIRES_ULTRASONIC_GENUINE) else \
            hires_profile(ffmpeg, samples[0], windows[samples[0]])
        declared = '%s/%s' % (bit_depth or '?', ('%g' % (sample_rate / 1000.0)))
        out['hires'] = dict(hires_verdict(ultra, profile, declared), ultrasonic_db=ultra, profile=profile)
    if lossless and bit_depth >= 24:
        bits = used_bits(ffmpeg, samples[0], windows[samples[0]])
        out['used_bits'] = bits
        if bits and bits <= 16:
            out['bitdepth_note'] = ('Declared %d-bit but only %d bits carry data: looks like padding '
                                    '(absence of this note is not proof of real 24-bit).' % (bit_depth, bits))
    return out

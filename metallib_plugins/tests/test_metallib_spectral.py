# SPDX-License-Identifier: GPL-2.0-or-later
# Spectral check on generated audio with KNOWN truth (ffmpeg needed; skipped otherwise).
import os
from pathlib import Path
import subprocess
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'metallib_spectral'))
import spectral as sp  # noqa: E402


FF = sp.find_ffmpeg()
pytestmark = pytest.mark.skipif(not FF, reason='ffmpeg is needed')


def _noise(sr):
    return ['-f', 'lavfi', '-i', 'anoisesrc=color=pink:sample_rate=%d:duration=25' % sr]


def _run(*args):
    subprocess.run([FF, '-v', 'error', '-y', *args], check=True)


@pytest.fixture(scope='module')
def audio(tmp_path_factory):
    d = tmp_path_factory.mktemp('spectral')
    files = {}

    def make(name, *args):
        files[name] = str(d / name)
        _run(*args, files[name])

    make('genuine.flac', *_noise(44100), '-sample_fmt', 's16')
    make('quiet.flac', *_noise(44100), '-af', 'volume=-50dB', '-sample_fmt', 's16')
    make('dark.flac', *_noise(44100), '-af', 'lowpass=f=12000,lowpass=f=12000,lowpass=f=12000', '-sample_fmt', 's16')
    for kbps, cut in (('128k', 16000), ('320k', 20500)):
        mp3 = str(d / ('src%s.mp3' % kbps))
        _run(*_noise(44100), '-b:a', kbps, '-cutoff', str(cut), mp3)
        make('fake%s.flac' % kbps, '-i', mp3, '-sample_fmt', 's16')
        files['src%s.mp3' % kbps] = mp3
    make('up320.mp3', '-i', files['src128k.mp3'], '-b:a', '320k')
    make('hr_genuine.flac', *_noise(96000), '-sample_fmt', 's32')
    make('hr_up.flac', *_noise(44100), '-af', 'aresample=96000', '-sample_fmt', 's32')
    return files


def test_genuine_quiet_and_dark_are_never_accused(audio):
    for name in ('genuine.flac', 'quiet.flac'):
        assert sp.check_album([audio[name]])['verdict'] == sp.CLEAN, name
    dark = sp.check_album([audio['dark.flac']])
    assert dark['verdict'] == sp.UNCERTAIN and 'no brick wall' in dark['label']       # dark master, not fake


def test_transcodes_are_caught(audio):
    t128 = sp.check_album([audio['fake128k.flac']])
    assert t128['verdict'] == sp.TRANSCODE and t128['shape']['sharp']
    assert sp.check_album([audio['fake320k.flac']])['verdict'] == sp.SUSPICIOUS


def test_upscaled_mp3_vs_genuine(audio):
    assert sp.check_album([audio['up320.mp3']], claimed_kbps=320)['label'] == '⚠ Likely upscaled / re-encoded'
    assert sp.check_album([audio['src320k.mp3']], claimed_kbps=320)['verdict'] == sp.CLEAN


def test_hires(audio):
    assert sp.check_album([audio['hr_genuine.flac']], sample_rate=96000, bit_depth=24)['hires']['verdict'] == 'genuine'
    assert sp.check_album([audio['hr_up.flac']], sample_rate=96000, bit_depth=24)['hires']['verdict'] == 'upsampled'


def test_shape_ladder_brackets_the_edge():
    assert sp.shape_ladder(17.0) == list(range(15000, 18501, 500))
    assert sp.shape_ladder(22.05)[-1] == 22000
    assert sp.shape_ladder(None) == sp.FINE_LADDER


def test_sampling_skips_pregap_silence(tmp_path):
    big = [tmp_path / ('%02d song.flac' % i) for i in range(1, 6)]
    for i, p in enumerate(big):
        p.write_bytes(b'x' * (700_000 + i * 1000))
    gap = tmp_path / '00 [silence].flac'
    gap.write_bytes(b'x' * 54_000)
    picks = sp.sample_files([str(gap)] + [str(p) for p in big])
    assert str(gap) not in picks and len(picks) == 3

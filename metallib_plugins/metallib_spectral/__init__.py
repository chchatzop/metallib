# MetalLib spectral check -- right-click an album or cluster -> "Spectral check...".
#
# Detects fake lossless (FLAC transcoded from lossy), lossy files upscaled to a higher bitrate, and
# hi-res files upsampled from CD, by measuring the audio's real bandwidth with ffmpeg (see
# spectral.py for the method and its calibration). ADVISORY ONLY: it changes nothing on disk and
# never edits a tag; the verdict is shown in a dialog and as %_spectral% (for a custom column).
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial

from PyQt6 import QtWidgets

from picard.plugin3.api import (
    Album,
    BaseAction,
    Cluster,
    PluginApi,
)
from picard.util import thread

from .spectral import (
    check_album,
    find_ffmpeg,
)


_api = None


def _files_of(obj):
    if isinstance(obj, Album):
        return list(obj.iterfiles())
    if isinstance(obj, Cluster):
        return list(obj.iterfiles())
    return []


def _declared(files):
    """What the files CLAIM: bitrate (+VBR) for lossy, sample rate / bit depth for lossless."""
    first = files[0]
    md = first.orig_metadata
    q = getattr(first, '_metallib_quality', None) or {}       # from the MetalLib Quality plugin, if loaded
    kbps = round(float(md['~bitrate'] or 0)) or None
    try:
        rate, bits = int(md['~sample_rate'] or 0), int(md['~bits_per_sample'] or 0)
    except ValueError:
        rate, bits = 0, 0
    return {'claimed_kbps': kbps if not bits else None,
            'is_vbr': bool(q.get('vbr') or q.get('encoder_settings')),
            'sample_rate': rate, 'bit_depth': bits}


class SpectralCheck(BaseAction):
    TITLE = "Spectral check..."

    def callback(self, objs):
        if not find_ffmpeg():
            QtWidgets.QMessageBox.warning(self.tagger.window, 'MetalLib spectral check',
                                          'ffmpeg was not found on PATH; the spectral check needs it.')
            return
        for obj in objs:
            files = _files_of(obj)
            if not files:
                continue
            name = obj.metadata['album'] or str(obj)
            self.tagger.window.set_statusbar_message('MetalLib: spectral check of "%s"...', name)
            thread.run_task(partial(check_album, [f.filename for f in files], **_declared(files)),
                            partial(_done, name, files))


def _done(name, files, result=None, error=None):
    tagger = _api.tagger
    if error or not result or not result.get('ok'):
        why = error or (result or {}).get('error') or 'unknown error'
        tagger.window.set_statusbar_message('MetalLib: spectral check of "%s" failed: %s', name, why)
        return
    short = result['label']
    if result.get('hires'):
        short += ' | ' + result['hires']['label']
    for f in files:
        f.metadata['~spectral'] = short
        f.update()
    lines = ['<b>%s</b>' % _html(name), '<b>%s</b> (%s confidence)' % (_html(result['label']), result['confidence']),
             _html(result['explanation']),
             'Measured bandwidth: ~%s kHz on the best of %d sampled track(s).' % (result['eff_khz'],
                                                                                  result['files_checked'])]
    if result.get('shape'):
        lines.append('Roll-off: steepest %.0f dB in one 0.5 kHz step, ending at ~%.1f kHz (a cliff is >= 16 dB).'
                     % (result['shape']['max_drop_db'], result['shape']['cutoff_khz']))
    if result.get('hires'):
        lines += ['', '<b>%s</b>' % _html(result['hires']['label']), _html(result['hires']['explanation'])]
    if result.get('bitdepth_note'):
        lines += ['', _html(result['bitdepth_note'])]
    lines += ['', '<i>Advisory only: nothing was changed. When in doubt, look at a spectrogram.</i>']
    QtWidgets.QMessageBox.information(tagger.window, 'MetalLib spectral check', '<br>'.join(lines))
    tagger.window.set_statusbar_message('MetalLib: "%s": %s', name, short)


def _html(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    api.register_album_action(SpectralCheck)
    api.register_cluster_action(SpectralCheck)
    api.register_script_variable('_spectral', documentation='Result of the last MetalLib spectral check.')

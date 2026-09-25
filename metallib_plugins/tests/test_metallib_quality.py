# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
from pathlib import Path
import sys
import types

import pytest

from picard.script import ScriptParser


PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'metallib_quality'
sys.path.insert(0, str(PLUGIN_DIR))
from quality import (  # noqa: E402
    MIXED,
    album_label,
    file_label,
    lame_tier,
)


def mp3(kbps, enc='', vbr=False):
    return {'mp3': True, 'lossless': False, 'kbps': kbps, 'encoder_settings': enc, 'vbr': vbr}


def flac(bits, sr):
    return {'mp3': False, 'lossless': True, 'bits': bits, 'sample_rate': sr}


@pytest.mark.parametrize('enc,kbps,expected', [
    ('-V 0', 250, 'V0'),
    ('-V 2 --vbr-new', 230, 'V2'),        # old --vbr-new runs hot: still V2, not a band guess
    ('--alt-preset standard', 200, 'V2'),
    ('--preset fast extreme', 250, 'V0'),
    ('--alt-preset insane', 320, '320K'),
    ('--preset 320', 320, '320K'),
    ('--preset 256', 250, ''),            # ABR 256 is not a V tier (Alghazanth case)
    ('--abr 200', 200, ''),
    ('-V 6', 319, ''),                    # header contradicted by the audio (-b 320 floor)
    ('', 245, ''),                        # no LAME header: never guess
])
def test_lame_tier(enc, kbps, expected):
    assert lame_tier(enc, kbps) == expected


@pytest.mark.parametrize('q,expected', [
    (flac(16, 44100), '16-44'),
    (flac(24, 88200), '24-88'),
    (flac(24, 192000), '24-192'),
    (mp3(320), '320K'),
    (mp3(128, '-b 128'), '128K'),
    (mp3(192, '-V 2'), 'V2'),             # recorded tier beats the CBR snap
    (mp3(193, vbr=True), 'VBR'),          # declared VBR is not snapped to 192K
    (mp3(203), 'VBR'),
    ({'mp3': False, 'lossless': False, 'kbps': 160}, ''),   # Ogg/Opus/AAC: no label
])
def test_file_label(q, expected):
    assert file_label(q) == expected


def test_album_uniform():
    assert album_label([flac(24, 96000)] * 8) == '24-96'
    assert album_label([mp3(250, '-V 0'), mp3(238, '-V 0')]) == 'V0'
    assert album_label([mp3(320)] * 10) == '320K'


def test_album_headerless_vbr_is_one_group():
    # Amnio case: one continuous VBR run, one track happens to average 255 (would snap to 256K).
    assert album_label([mp3(k) for k in (215, 229, 241, 255, 263, 298)]) == 'VBR'


def test_album_mixed():
    # Alltid Allena / Aghar: one odd track in a hi-res album.
    assert album_label([flac(24, 44100)] * 6 + [flac(16, 44100)]) == MIXED
    assert album_label([flac(24, 48000)] * 6 + [flac(24, 44100)]) == MIXED
    assert album_label([mp3(250, '-V 0'), mp3(190, '-V 2')]) == MIXED
    assert album_label([flac(16, 44100), mp3(320)]) == MIXED
    # Arterial Hemorrhage: genuine CBR files at different rates are not "one VBR album".
    assert album_label([mp3(32), mp3(160), mp3(32), mp3(128)]) == MIXED


def test_album_empty():
    assert album_label([]) == ''
    assert album_label([None]) == ''


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        'picard.plugins.metallib_quality_test', PLUGIN_DIR / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- Integration with real Picard Album/Track/Cluster objects and the real script engine -------

from test.picardtestcase import PicardTestCase  # noqa: E402

from picard.album import Album  # noqa: E402
from picard.extension_points import (  # noqa: E402
    set_plugin_uuid,
    unregister_module_extensions,
)
from picard.extension_points.script_functions import register_script_function  # noqa: E402
from picard.cluster import Cluster  # noqa: E402
from picard.file import File  # noqa: E402
from picard.track import Track  # noqa: E402


TEST_UUID = '5c6b0a9c-23ef-4a1e-9d1d-7ca8a7d46692'


class TestAlbumQualityInPicard(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.plugin = _load_plugin_module()
        self.tagger.albums = {}
        self.tagger.clusters = []
        self.plugin._api = types.SimpleNamespace(tagger=self.tagger)
        # Enable it the way Picard does: extension points only run enabled plugins' items.
        set_plugin_uuid(TEST_UUID, 'metallib_quality_test')
        self.set_config_values(setting={'plugins3_enabled_plugins': [TEST_UUID]})
        register_script_function(self.plugin.album_quality, name='album_quality')
        register_script_function(self.plugin.quality, name='quality')
        self.addCleanup(unregister_module_extensions, 'picard.plugins.metallib_quality_test')

    def _file(self, name, q):
        f = File(name)
        setattr(f, self.plugin._ATTR, q)
        return f

    def _eval(self, context, file=None):
        return ScriptParser().eval('$album_quality()', context, file)

    def test_album_row_and_file_rows(self):
        album = Album('00000000-0000-0000-0000-000000000001')
        self.tagger.albums[album.id] = album
        files = [self._file('t%d.flac' % n, flac(24, 48000)) for n in range(6)]
        files.append(self._file('t6.flac', flac(24, 44100)))           # Aghar: one 44.1 kHz track
        for n, f in enumerate(files):
            track = Track('00000000-0000-0000-0000-0000000001%02d' % n, album)
            track.files.append(f)
            f.parent_item = track
            album.tracks.append(track)
        # Album row: no file, only the album's Metadata.
        self.assertEqual(self._eval(album.metadata), MIXED)
        # File row / naming script: every file of the album gets the same folder tag.
        self.assertEqual({self._eval(f.metadata, f) for f in files}, {MIXED})
        album.tracks[-1].files.clear()                                  # drop the odd track
        self.assertEqual(self._eval(album.metadata), '24-48')

    def test_cluster_row(self):
        cluster = Cluster('Grey Metal')
        self.tagger.clusters.append(cluster)
        for n in range(3):
            f = self._file('c%d.mp3' % n, mp3(250, '-V 0'))
            cluster.files.append(f)
            f.parent_item = cluster
        self.assertEqual(self._eval(cluster.metadata), 'V0')

    def test_unclustered_file_uses_own_label(self):
        f = self._file('lone.flac', flac(16, 44100))
        f.metadata['~quality'] = '16-44'
        self.assertEqual(self._eval(f.metadata, f), '16-44')


class TestQualityColumn(TestAlbumQualityInPicard):
    """$quality() through Picard's real custom-column value provider, on every row type."""

    def _column(self, obj):
        from picard.ui.itemviews.custom_columns.script_provider import ChainedValueProvider
        return ChainedValueProvider('$quality()').evaluate(obj)

    def test_rows(self):
        album = Album('00000000-0000-0000-0000-000000000002')
        self.tagger.albums[album.id] = album
        files = [self._file('t%d.flac' % n, flac(24, 48000)) for n in range(3)]
        files.append(self._file('t3.flac', flac(24, 44100)))          # the odd track
        for n, f in enumerate(files):
            track = Track('00000000-0000-0000-0000-0000000002%02d' % n, album)
            track.files.append(f)
            f.parent_item = track
            album.tracks.append(track)
        empty = Track('00000000-0000-0000-0000-000000000299', album)  # track with no file
        album.tracks.append(empty)

        cluster = Cluster('Some cluster', related_album=album)       # clusters have .album too
        self.tagger.clusters.append(cluster)
        cf = self._file('c.mp3', mp3(250, '-V 0'))
        cluster.files.append(cf)
        cf.parent_item = cluster

        self.assertEqual(self._column(album), MIXED)
        self.assertEqual([self._column(t) for t in album.tracks[:4]], ['24-48'] * 3 + ['24-44'])
        self.assertEqual([self._column(f) for f in files], ['24-48'] * 3 + ['24-44'])
        self.assertEqual(self._column(empty), '')
        self.assertEqual(self._column(cluster), 'V0')
        self.assertEqual(self._column(cf), 'V0')


def test_lossless_by_format_not_by_bit_depth():
    # Audit part 2 M4: mutagen gives AAC in .m4a a bit depth (16) too -- it was labelled "16-44".
    import mutagen
    plugin = _load_plugin_module()
    aac = mutagen.File(str(Path(__file__).resolve().parents[2] / 'test' / 'data' / 'test.m4a'))
    fmt = 'MPEG-4 Audio (%s)' % aac.info.codec_description
    assert not plugin.is_lossless('.m4a', fmt, aac.info.bits_per_sample)
    assert plugin.is_lossless('.m4a', 'MPEG-4 Audio (ALAC)', 16)
    assert plugin.is_lossless('.FLAC', 'FLAC', 24) and not plugin.is_lossless('.flac', 'FLAC', 0)
    assert not plugin.is_lossless('.ogg', 'Vorbis', 16)

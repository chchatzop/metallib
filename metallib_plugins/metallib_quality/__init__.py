# MetalLib quality -- exposes each file's audio quality to the naming script.
#
#   %_quality%          this file:  16-44, 24-96, V0, V2, 320K, 192K, VBR ('' if unknown)
#   %_lame_settings%    MP3 only: the encoder settings LAME recorded, e.g. "-V 2 --vbr-new"
#   $album_quality()    the label every file of this file's album/cluster agrees on,
#                       "Mixed" when they disagree. Use this in the naming script.
#   $quality()          for a custom column: album/cluster rows show the album label (Mixed),
#                       track/file rows show their own, so the odd track is visible on expand.
#
# Right-click an album or cluster -> "Quality breakdown..." lists which tracks are which quality.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os

from mutagen.mp3 import (
    MP3,
    BitrateMode,
)

from PyQt6 import QtWidgets

from picard.plugin3.api import (
    Album,
    BaseAction,
    Cluster,
    PluginApi,
    Track,
)

from .quality import (
    MIXED,
    album_label,
    file_label,
    group_labels,
)


_ATTR = '_metallib_quality'     # raw per-file quality dict, stored on the File object
_api = None                     # set in enable(); script functions don't receive the api


def _long_path(path):
    # Picard itself opens >260-char paths, but mutagen needs the \\?\ prefix on Windows.
    if os.name == 'nt' and len(path) >= 260 and not path.startswith('\\\\?\\'):
        if path.startswith('\\\\'):
            return '\\\\?\\UNC\\' + path[2:]
        return '\\\\?\\' + path
    return path


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _read_quality(api, file):
    md = file.orig_metadata
    bits = _int(md['~bits_per_sample'])
    q = {
        'lossless': bool(bits),
        'bits': bits,
        'sample_rate': _int(md['~sample_rate']),
        'kbps': round(float(md['~bitrate'] or 0)),
        'mp3': os.path.splitext(file.filename)[1].lower() == '.mp3',
        'vbr': False,
        'encoder_settings': '',
    }
    if q['mp3']:
        try:
            info = MP3(_long_path(file.filename)).info
            q['encoder_settings'] = str(getattr(info, 'encoder_settings', '') or '')
            q['vbr'] = getattr(info, 'bitrate_mode', None) in (BitrateMode.VBR, BitrateMode.ABR)
            if info.bitrate:
                q['kbps'] = round(info.bitrate / 1000)
        except Exception as e:
            api.logger.warning("Could not read MP3 header of %r: %s", file.filename, e)
    return q


def _apply(file):
    q = getattr(file, _ATTR, None)
    if q is None:
        return
    label = file_label(q)
    for md in (file.metadata, file.orig_metadata):
        md['~quality'] = label
        if q['encoder_settings']:
            md['~lame_settings'] = q['encoder_settings']


def on_file_loaded(api, file):
    setattr(file, _ATTR, _read_quality(api, file))
    _apply(file)
    api.logger.debug("quality %r (lame %r): %s", file.metadata['~quality'],
                     file.metadata['~lame_settings'], file.filename)


def on_file_added_to_track(api, track, file):
    # Matching to a track with "clear existing tags" rebuilds file.metadata and drops custom ~vars.
    _apply(file)


def _container_files(item):
    """Files of an Album (matched only) or a real Cluster; None for anything else."""
    if isinstance(item, Album):
        return list(item.iterfiles(save=True))
    if isinstance(item, Cluster) and not item.special:       # not "Unclustered Files"
        return list(item.iterfiles())
    return None


def _sibling_files(file):
    parent = file.parent_item
    files = _container_files(parent.album if isinstance(parent, Track) else parent)
    return files if files is not None else [file]


def _owner_of_metadata(metadata):
    # Album/cluster/track rows evaluate scripts with only the row's Metadata and (usually) no
    # file, so find which loaded album, track or cluster owns that Metadata object.
    tagger = _api.tagger if _api else None
    if tagger is None or metadata is None:
        return None
    for album in tagger.albums.values():
        if album.metadata is metadata:
            return album
        for track in album.tracks:
            if track.metadata is metadata:
                return track
    for cluster in tagger.clusters:
        if cluster.metadata is metadata:
            return cluster
    return None


def _container_of_metadata(metadata):
    owner = _owner_of_metadata(metadata)
    return None if isinstance(owner, Track) else owner


def album_quality(parser):
    if parser.file is not None:
        files = _sibling_files(parser.file)
    else:
        files = _container_files(_container_of_metadata(parser.context))
        if files is None:
            return parser.context['~quality']
    return album_label([getattr(f, _ATTR, None) for f in files])


def quality(parser):
    """Per-row quality for a custom column: albums/clusters get the album label (Mixed), tracks
    and files get their own, so expanding a Mixed album shows which track is the odd one."""
    if parser.file is not None:
        return file_label(getattr(parser.file, _ATTR, None)) or parser.context['~quality']
    owner = _owner_of_metadata(parser.context)
    if owner is None:
        return parser.context['~quality']
    if isinstance(owner, Track):
        files = list(owner.files)
    else:
        files = _container_files(owner) or []
    return album_label([getattr(f, _ATTR, None) for f in files])


class QualityBreakdown(BaseAction):
    TITLE = "Quality breakdown..."

    def callback(self, objs):
        parts = []
        for obj in objs:
            files = _container_files(obj) or []
            groups = group_labels([getattr(f, _ATTR, None) for f in files])
            label = album_label([getattr(f, _ATTR, None) for f in files]) or 'unknown'
            lines = ["<b>%s</b>: %s" % (_html(obj.metadata['album'] or str(obj)), label)]
            if label == MIXED:
                for q_label, idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                    lines.append("&nbsp;&nbsp;%s — %d file(s)" % (q_label, len(idxs)))
                    # List the odd ones out by name; the majority group is just counted.
                    if len(idxs) < max(len(v) for v in groups.values()):
                        for i in idxs:
                            lines.append("&nbsp;&nbsp;&nbsp;&nbsp;%s" % _html(os.path.basename(files[i].filename)))
            parts.append("<br>".join(lines))
        QtWidgets.QMessageBox.information(
            self.tagger.window, "Quality breakdown", "<br><br>".join(parts) or "Nothing selected.")


def _html(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def enable(api: PluginApi) -> None:
    global _api
    _api = api
    api.register_album_action(QualityBreakdown)
    api.register_cluster_action(QualityBreakdown)
    api.register_file_post_load_processor(on_file_loaded)
    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_script_function(
        album_quality,
        name='album_quality',
        documentation=(
            "`$album_quality()`\n\n"
            "Quality label shared by every file of this file's album or cluster "
            "(e.g. `16-44`, `24-96`, `V0`, `320K`, `VBR`), or `Mixed` when they disagree."
        ),
    )
    api.register_script_function(
        quality,
        name='quality',
        documentation=(
            "`$quality()`\n\n"
            "For a custom column: album and cluster rows show the album's quality (`Mixed` when "
            "its files disagree), track and file rows show their own (e.g. `24-96`, `V0`)."
        ),
    )
    api.register_script_variable(
        '_quality',
        documentation="This file's quality: 16-44, 24-96, V0..V9, 320K, 192K ..., VBR.",
    )
    api.register_script_variable(
        '_lame_settings',
        documentation="MP3 only: the encoder settings recorded in the LAME header, e.g. -V 2 --vbr-new.",
    )

# SPDX-License-Identifier: GPL-2.0-or-later
# The Picard internals MetalLib's plugins hook into or call (audit part 1 L8, part 2 L6).
#
# Picard 3 has no plugin hooks for these, so the plugins replace these functions. Replacing an attribute
# never fails: if Picard renames or changes one of them, the MetalLib feature would just silently stop.
# Run this after every merge of upstream Picard -- a failure names the function to adapt.
import inspect

import pytest

from picard import (
    album as picard_album,
    cluster as picard_cluster,
    file as picard_file,
    tagger as picard_tagger,
)
from picard.session import session_loader
from picard.ui.itemviews import basetreeview


HOOKS = [
    # (owner, name, parameters, used by)
    (picard_file.File, '_save_and_rename', ['self', 'old_filename', 'metadata'], 'metallib_undo: snapshot before a save'),
    (picard_file.File, '_saving_finished', ['self', 'result', 'error'], 'metallib_undo: where the file ended up'),
    (picard_file.File, '_move_additional_files', ['self', 'old_filename', 'new_filename', 'config'],
     'metallib_folder: the Extra files step replaces it for album files'),
    (picard_file.File, '_guess_tracknumber_and_title', ['self', 'metadata'], 'metallib_tracks: title only, never a number'),
    (picard_file, 'tracknum_and_title_from_filename', ['base_filename'], 'metallib_tracks: title guess fallback'),
    (picard_album.Album, 'match_files', ['self', 'files'], 'metallib_tracks: placement by title + duration'),
    (picard_album.Album, '_parse_release', ['self', 'release_node'], 'metallib_ma: Metal Archives albums'),
    (picard_album.Album, '_finalize_loading', ['self', 'error'], 'metallib_ma: Metal Archives albums'),
    (picard_album.Album, '_finalize_loading_track', ['self', 'track_node', 'metadata', 'artists', 'extra_metadata'],
     'metallib_ma: Metal Archives albums'),
    (picard_cluster.Cluster, 'add_files', ['self', 'files', 'new_album'], 'metallib_tracks: renumbering'),
    (picard_cluster.Cluster, '_lookup_finished', ['self', 'document', 'http', 'error'],
     'metallib_ma: Lookup falls through to Metal Archives'),
    (picard_cluster, 'album_artist_from_path', ['filename', 'album', 'artist'], 'metallib_ma: folder names'),
    (picard_tagger.Tagger, 'load_album', ['self', 'album_id', 'discid', 'disc_isrcs'], 'metallib_ma: session restore'),
    (picard_tagger.Tagger, 'move_file_to_nat', ['self', 'file', 'recordingid', 'node'],
     'metallib_ma: files saved with "Album info only"'),
    (picard_tagger.Tagger, '_file_loaded', ['self', 'file', 'target', 'remove_file', 'unmatched_files'],
     'metallib_ma: recognised by name in _move_file_to_nat'),
    (session_loader.AlbumManager, 'load_album_with_strategy', ['self', 'album_id', 'cached_node'],
     'metallib_ma: session restore'),
    (session_loader.AlbumManager, '_build_from_cache', ['self', 'album_id', 'node'], 'metallib_ma: session restore'),
    (basetreeview.BaseTreeView, '_set_header_labels', ['self', 'update_column_count'],
     'metallib_tracks: the File column appears without a restart (called, not replaced)'),
]


@pytest.mark.parametrize('owner,name,params,used_by', HOOKS, ids=[h[1] for h in HOOKS])
def test_hooked_picard_function_is_unchanged(owner, name, params, used_by):
    func = getattr(owner, name, None)
    assert callable(func), 'Picard no longer has %s.%s (%s)' % (owner.__name__, name, used_by)
    assert list(inspect.signature(func).parameters) == params, \
        'Picard changed %s.%s -- adapt %s' % (owner.__name__, name, used_by)


def test_file_loaded_still_sends_files_to_standalone_recordings():
    # metallib_ma's _move_file_to_nat recognises this caller by its name (sys._getframe)
    assert 'move_file_to_nat' in inspect.getsource(picard_tagger.Tagger._file_loaded)

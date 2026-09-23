# MetalLib -- fill the per-source columns of the tag panel ("MusicBrainz", "Metal Archives").
#
# Every track may carry track.source_metadata = {source name: Metadata} (see
# picard/ui/metadatabox/sources.py). This module produces those Metadata objects from a release
# node through Picard's OWN release parser (a never-shown "shadow" album), so both columns use
# exactly the tag names and formatting a normally loaded album would, and pairs the other
# release's tracks with the album's tracks by title + duration.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from picard.album import Album
from picard.metadata import Metadata
from picard.releasegroup import ReleaseGroup

from .ma_release import pair_tracks


MUSICBRAINZ = 'MusicBrainz'
METAL_ARCHIVES = 'Metal Archives'


class ShadowAlbum(Album):
    """Parses a release node like a real album, but is never registered, shown or matched."""

    def _setup_release_group(self, release_node):
        # A private release group: never enters tagger.release_groups, so it cannot leak into or
        # be shared with a real album.
        rg_node = release_node['release-group']
        self.release_group = ReleaseGroup(rg_node['id'])
        from picard.album import (
            _copy_artist_nodes,
            release_group_to_metadata,
        )
        _copy_artist_nodes(self._release_artist_nodes, rg_node)
        release_group_to_metadata(rg_node, self.release_group.metadata, self.release_group)


def track_metadata(release_node, fix=None):
    """[Metadata per track, in release order] for a release node, via Picard's parser.
    `fix(metadata)` may post-process each (e.g. drop placeholder ids, add band facts)."""
    shadow = ShadowAlbum(release_node['id'])
    shadow._new_metadata = Metadata()
    shadow._new_tracks = []
    shadow._parse_release(release_node)
    shadow._load_tracks()
    out = []
    for track in shadow._new_tracks:
        md = Metadata()
        md.copy(track.metadata)
        if fix:
            fix(md)
        out.append(md)
    return out


def _pairing_info(md):
    return {'title': md['title'], 'length': md.length or 0,
            'disc': md['discnumber'] or '1', 'number': md['tracknumber']}


def attach(album, source, source_mds):
    """Pair `source_mds` with `album`'s tracks and store them as that source's column values.
    Returns how many tracks got a value."""
    tracks = list(album.tracks)
    pairs = pair_tracks([_pairing_info(t.metadata) for t in tracks],
                        [_pairing_info(md) for md in source_mds])
    for ti, si in pairs.items():
        sources = getattr(tracks[ti], 'source_metadata', None) or {}
        sources[source] = source_mds[si]
        tracks[ti].source_metadata = _ordered(sources)
    return len(pairs)


def set_own_source(track, source, metadata):
    """The album's own source: a snapshot of what it said, taken while the track is built."""
    md = Metadata()
    md.copy(metadata)
    sources = getattr(track, 'source_metadata', None) or {}
    sources[source] = md
    track.source_metadata = _ordered(sources)


def _ordered(sources):
    # Always MusicBrainz first, then Metal Archives, then Discogs, then anything else.
    order = [MUSICBRAINZ, METAL_ARCHIVES, 'Discogs']
    return dict(sorted(sources.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)))

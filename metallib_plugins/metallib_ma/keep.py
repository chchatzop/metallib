# MetalLib -- plain tags (user, 2026-09-25): MetalLib writes only the tags on this list; every other
# tag -- from a source (MusicBrainz relationships, the Metal Archives lineup) or already in the file
# (scene rips, other taggers) -- is removed.
#
# Names are Picard's internal ones; Picard writes some under two keys (totaltracks -> TOTALTRACKS
# and TRACKTOTAL, totaldiscs -> TOTALDISCS and DISCTOTAL) and some under another name in FLAC
# (musicbrainz_recordingid -> MUSICBRAINZ_TRACKID, musicbrainz_trackid -> MUSICBRAINZ_RELEASETRACKID).
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

KEEP = frozenset((
    # basics, with sort names and every artist of a split listed
    'title', 'artist', 'album', 'albumartist', 'artistsort', 'albumartistsort', 'artists', 'albumartists',
    # position
    'tracknumber', 'totaltracks', 'discnumber', 'totaldiscs', 'discsubtitle',
    # dates: the pressing's, the first release's
    'date', 'originaldate', 'originalyear',
    # genre, and what the user's genre pipeline writes and reads
    'genre', 'genre_source', 'ma_genre',
    # the pressing
    'label', 'catalognumber', 'catnum', 'media', 'releasecountry',
    # the user's own naming tags (they drive the folder name, see the naming script):
    # edition (Jap / Lim / Dlx ...), reissue (year), remaster (year), showcat (= 1: the catalog
    # bracket goes into file names too), albumversion (free text)
    'edition', 'reissue', 'remaster', 'showcat', 'albumversion',
    # release kind
    'releasetype', 'releasestatus',
    # ids
    'musicbrainz_recordingid', 'musicbrainz_releasegroupid', 'musicbrainz_artistid', 'musicbrainz_albumartistid',
    'musicbrainz_albumid', 'musicbrainz_trackid', 'musicbrainz_discid', 'acoustid_id',
    # the audio
    'encoder',
))
KEEP_PREFIXES = ('replaygain_',)
# kept only with a real MusicBrainz release, i.e. when the files get its release id
WITH_RELEASE = frozenset(('barcode', 'asin', 'isrc'))


def keeps(tag, has_release_id):
    if tag.startswith('~') or tag in KEEP or tag.startswith(KEEP_PREFIXES):
        return True
    return tag in WITH_RELEASE and has_release_id


def make_plain(md, user=()):
    """Remove from `md` (a Picard Metadata) every tag not kept; tags in `user` (picked by the user
    from a source column) stay. Returns the removed tag names."""
    has_release = bool(md['musicbrainz_albumid'])
    removed = [t for t in md if t not in user and not keeps(t, has_release)]
    # MusicBrainz's "there is none" placeholders are not values
    removed += [t for t, none in PLACEHOLDERS.items()
                if t in md and t not in user and t not in removed
                and all(v.strip().lower() == none for v in md.getall(t))]
    for tag in removed:
        del md[tag]
    return removed


PLACEHOLDERS = {'catalognumber': '[none]', 'label': '[no label]'}

# MetalLib naming -- script functions for the "MetalLib library layout" file naming script
# (MetalLib.ptsp in this folder; import it in Options -> File Naming -> Edit -> Import). The naming
# rules live in that script, editable there; these functions only supply what a script can't
# compute itself:
#
#   $clean(text[,title])      a name part the way the library writes it: ASCII (Cyrillic/Greek
#                             transliterated), ":" -> " -", "?" dropped, single spaces, no trailing
#                             dots -- with "title" a trailing "..." / "A.I.R." is kept
#   $first_letter(text)       letter folder: "Abbath" -> A, "Ångström" -> A, "1349" -> #
#   $album_media()            every medium of this file's album ("CD / DVD"), from New Value; a
#                             file's own %media% is only its disc's, which would split a CD+DVD set
#   $media_code(text)         source tag: Digital Media -> WEB, CD/SHM-CD -> CD, Vinyl -> LP, Cassette -> Tape
#   $folder_media(path)       what the folder name says it was ripped from: WEB / CD / LP / Tape
#   CATNUM tag                written at save: "catalog MEDIA" / "MEDIA" / "WEB" -- the folder's
#                             [ ] bracket (the user's own tag from their earlier tool)
#   $with_media(cat,code)     "SOM 650B" + CD -> "SOM 650B CD" (kept as is when it already ends in it)
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os

from picard.plugin3.api import (
    PluginApi,
    Track,
)

from .transliterate import clean as _clean
from .naming import (
    album_media as _album_media,
    first_letter as _first_letter,
    folder_media as _folder_media,
    media_code as _media_code,
    with_media as _with_media,
)


def clean(parser, text='', mode=''):
    # mode: '' drop trailing dots; 'all' keep them all (artist); anything else keep "..." / "A.I.R."
    return _clean(text, keep_trailing='all' if mode == 'all' else bool(mode))


def first_letter(parser, text=''):
    return _first_letter(text)


def media_code(parser, text=''):
    return _media_code(text)


def folder_media(parser, path=''):
    return _folder_media(path)


def with_media(parser, catalog='', code=''):
    return _with_media(catalog, code)


def album_media(parser):
    file = parser.file
    if file is not None and isinstance(file.parent_item, Track) and file.parent_item.album is not None:
        album = file.parent_item.album
        values = []
        for track in album.tracks:
            md = track.files[0].metadata if track.files else track.metadata
            values.append(md['media'])
        return _album_media(values)
    return parser.context['media']


def catnum_for(file):
    """The folder bracket as a tag, the same rule as the naming script: a WEB rip (folder name, or
    an earlier catnum WEB) -> WEB; else the album's media with the first catalog number."""
    md = file.metadata
    rip = _folder_media(md['~dirname'] or os.path.dirname(file.filename))
    if rip == 'WEB' or md['catnum'] == 'WEB':
        return 'WEB'
    code = _media_code(_file_album_media(file)) or rip
    catalog = (md.getall('catalognumber') or [''])[0]
    return _with_media(catalog, code) if catalog or code else ''


def _file_album_media(file):
    track = file.parent_item
    if isinstance(track, Track) and track.album is not None:
        return _album_media([(t.files[0].metadata if t.files else t.metadata)['media'] for t in track.album.tracks])
    return file.metadata['media']


def _typed_by_user(file):
    # a CATNUM set by hand in the tag panel (metadatabox sources.note_user_edit) is never replaced
    track = file.parent_item if isinstance(file.parent_item, Track) else None
    return any('catnum' in (getattr(o, 'value_sources', None) or {}) for o in (file, track) if o is not None)


def update_catnum(file):
    """CATNUM in New Value, so it can be seen (and changed) before saving -- it used to be written
    only at the moment of saving (user). Called when a file joins an album track, after the source
    rules changed the album's values (metallib_ma) and once more before saving."""
    if _typed_by_user(file):
        return False
    value = catnum_for(file)
    if value and file.metadata['catnum'] != value:     # nothing known: keep what the file has
        file.metadata['catnum'] = value
        return True
    return False


def on_file_added(api, track, file):
    update_catnum(file)


def on_file_saving(api, file):
    update_catnum(file)


def enable(api: PluginApi) -> None:
    api.register_file_pre_save_processor(on_file_saving)
    api.register_file_post_addition_to_track_processor(on_file_added)
    api.tagger.metallib_update_catnum = update_catnum      # metallib_ma calls it after its rules
    for func, name, doc in (
        (clean, 'clean',
         "`$clean(text[,title])`\n\nA name part as the MetalLib library writes it: plain ASCII (Cyrillic and "
         "Greek transliterated, accents folded), `:` becomes ` -`, `?` and `*` are dropped, spaces collapsed, "
         "trailing dots removed. With a second argument (for track titles) a trailing `...` or initialism "
         "(`A.I.R.`) is kept."),
        (first_letter, 'first_letter',
         "`$first_letter(text)`\n\nLetter folder for a name: `A`..`Z` (accents folded), `#` for digits "
         "and symbols. `$first_letter(Ångström)` = `A`, `$first_letter(1349)` = `#`."),
        (album_media, 'album_media',
         "`$album_media()`\n\nEvery medium of this file's album, e.g. `CD / DVD` (a file's own `%media%` "
         "is only its disc's)."),
        (media_code, 'media_code',
         "`$media_code(text)`\n\nSource tag for a media value: `WEB` (digital), `CD`, `LP` (vinyl), `Tape`, "
         "`DVD`, `BD`; the first medium of a mixed set."),
        (folder_media, 'folder_media',
         "`$folder_media(path)`\n\nWhat a folder name says the copy was ripped from: `WEB`, `CD`, `LP`, `Tape`, "
         "or empty. `$folder_media(%_dirname%)` for a scene folder like `Band-Album-WEB-2023-GRP` = `WEB`."),
        (with_media, 'with_media',
         "`$with_media(catalog,code)`\n\n`$with_media(SOM 650B,CD)` = `SOM 650B CD`; kept as is when the "
         "catalog already ends with the code (`TOR 105 LP`); just the code when there is no catalog."),
    ):
        api.register_script_function(func, name=name, documentation=doc)

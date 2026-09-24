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
#   $with_media(cat,code)     "SOM 650B" + CD -> "SOM 650B CD" (kept as is when it already ends in it)
#
# SPDX-License-Identifier: GPL-2.0-or-later

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
    return _clean(text, keep_trailing=bool(mode))


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


def enable(api: PluginApi) -> None:
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

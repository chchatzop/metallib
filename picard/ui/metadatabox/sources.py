# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
#
# Copyright (C) 2026 MetalLib contributors
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Per-source tag values shown as extra columns of the metadata box (MetalLib).

Any File or Track may carry ``source_metadata``: an ordered dict mapping a source name
(e.g. "MusicBrainz", "Metal Archives") to a Metadata object with what that source says.
A file without its own falls back to its track's. Plugins fill it; the metadata box shows one
read-only column per source and lets the user copy a source's value into New Value. Which
source a value was taken from is remembered in ``value_sources`` (tag -> source name).
"""

from picard.file import File
from picard.track import Track
from picard.util import format_time


DIFFERENT = object()        # the selected objects disagree for this source/tag


def object_sources(obj):
    """The source_metadata dict that applies to obj ({} if none)."""
    sources = getattr(obj, 'source_metadata', None)
    if not sources and isinstance(obj, File) and isinstance(obj.parent_item, Track):
        sources = getattr(obj.parent_item, 'source_metadata', None)
    return sources or {}


def source_objects(files, tracks):
    """The objects whose source values are shown: files, plus tracks without linked files
    (the same objects the metadata box compares)."""
    return list(files) + [t for t in tracks if not t.num_linked_files]


# A plugin may limit which source tags get a row of their own: row_filter(tag) -> bool. MetalLib
# shows only tags it would write (its keep-list); what the files already have always has a row.
row_filter = None


def source_tag_names(objects):
    """Tags some source has a value for, which every selected file can store (hidden ~tags
    excluded) and `row_filter` allows. These rows stay visible even after the user deletes the tag
    from New Value."""
    tags = set()
    for obj in objects:
        for md in object_sources(obj).values():
            tags.update(t for t in md if not t.startswith('~'))
    if row_filter is not None:
        tags = {t for t in tags if row_filter(t)}
    files = [o for o in objects if isinstance(o, File)]
    return {t for t in tags if all(f.supports_tag(t) for f in files)}


def _values(md, tag):
    """A source's values for one row. The Length row reads '~length', which a source snapshot
    taken while Picard builds the track may not have yet: then the raw length is shown."""
    if md is None:
        return []
    if tag == '~length':
        # The raw length first: a snapshot may carry '~length' EMPTY (MB column with files matched).
        if md.length:
            return [format_time(md.length)]
        return [v for v in md.getall(tag) if v]
    if tag in md:
        return list(md.getall(tag))
    return []


LENGTH_DIFFERS_S = 10


def _seconds(text):
    parts = str(text or '').strip().split(':')
    try:
        if len(parts) == 1:
            return int(parts[0]) / 1000.0            # New Value: milliseconds
        secs = 0
        for p in parts:
            secs = secs * 60 + int(p)
        return secs                                  # a source: "m:ss" / "h:mm:ss"
    except ValueError:
        return None


def length_differs(source_values, new_values):
    """A source's length vs the file's, more than LENGTH_DIFFERS_S apart."""
    a = _seconds(source_values[0]) if source_values else None
    b = _seconds(new_values[0]) if new_values else None
    return a is not None and b is not None and abs(a - b) > LENGTH_DIFFERS_S


def collect(objects, tag_names):
    """-> {source name: {tag: list of values | DIFFERENT}} for the given rows.

    A source appears when at least one object has it. Values are per tag across all objects:
    identical everywhere -> that list; otherwise DIFFERENT. An object lacking the source or the
    tag counts as an empty value.
    """
    names = []
    for obj in objects:
        for name in object_sources(obj):
            if name not in names:
                names.append(name)
    result = {}
    for name in names:
        per_tag = {}
        for tag in tag_names:
            seen = None
            for obj in objects:
                values = _values(object_sources(obj).get(name), tag)
                if seen is None:
                    seen = values
                elif values != seen:
                    seen = DIFFERENT
                    break
            per_tag[tag] = seen if seen is not None else []
        result[name] = per_tag
    return result


LABEL_TAG = '~source_label'   # hidden tag in a source's Metadata: what it shows, e.g. the pressing


def labels(objects):
    """-> {source name: label} for the column headers: the source's LABEL_TAG when every object
    that has the source agrees, "several" when they differ; sources without a label are left out."""
    seen = {}
    for obj in objects:
        for name, md in object_sources(obj).items():
            label = md[LABEL_TAG] if LABEL_TAG in md else ''
            if label:
                seen.setdefault(name, set()).add(label)
    return {name: (next(iter(values)) if len(values) == 1 else 'several') for name, values in seen.items()}


def use_source_value(objects, source, tag, apply_tag_values):
    """Copy each object's own value from `source` for `tag` into its metadata (New Value).
    Returns the objects that changed. Objects without that source/tag are left alone."""
    changed = []
    for obj in objects:
        if not isinstance(obj, (File, Track)):
            continue
        md = object_sources(obj).get(source)
        if md is None or tag not in md:
            continue
        changed.extend(apply_tag_values([obj], tag, list(md.getall(tag))))
        record = getattr(obj, 'value_sources', None)
        if record is None:
            record = obj.value_sources = {}
        record[tag] = source
    return changed


def cell_values(box, row, column):
    """Values shown in a source-column cell of the metadata box ([] when empty or differing), or
    None when `column` is not a source column. A plain function (not a MetadataBox method) so
    stand-in boxes in upstream tests, which have no source columns, keep working."""
    names = getattr(box, '_source_names', None)
    tag_diff = getattr(box, 'tag_diff', None)
    if not names or tag_diff is None:
        return None
    index = column - (box.COLUMN_NEW + 1)
    if not 0 <= index < len(names):
        return None
    values = tag_diff.sources.get(names[index], {}).get(tag_diff.tag_names[row], [])
    return [] if values is DIFFERENT else list(values)

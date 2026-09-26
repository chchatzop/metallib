# MetalLib -- the "File" column in the album pane (user, 2026-09-26): each track's current file name
# right before the title it will get, so both can be read side by side.
#
# Added ONCE, as an ordinary Picard custom column (script %_filename%): afterwards it can be moved,
# resized, hidden or deleted like any other (right-click the header -> Manage custom columns...), and
# it is not added again. A custom column of the user's own that already shows %_filename% counts.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from functools import partial

from PyQt6 import QtCore


KEY = 'metallib_file'
OPTION = 'file_column_added'


def _has_filename_column(specs):
    return any(s.key == KEY or '_filename' in (s.expression or '') for s in specs)


def install(api, tries=100):
    window = getattr(api.tagger, 'window', None)
    if window is None or not hasattr(window, 'panel'):
        if tries:
            QtCore.QTimer.singleShot(200, partial(install, api, tries - 1))
        return
    try:
        if api.plugin_config[OPTION]:
            return                                  # added once already: the user's to change now
    except KeyError:
        pass
    from picard.ui.itemviews import AlbumTreeView
    from picard.ui.itemviews.custom_columns.shared import VIEW_ALBUM
    from picard.ui.itemviews.custom_columns.storage import (
        CustomColumnKind,
        CustomColumnSpec,
        load_specs_from_config,
        register_and_persist,
    )
    if not _has_filename_column(load_specs_from_config()):
        register_and_persist(CustomColumnSpec(title='File', key=KEY, kind=CustomColumnKind.SCRIPT,
                                              expression='%_filename%', width=260, add_to=VIEW_ALBUM))
        view = next((v for v in window.panel._views if isinstance(v, AlbumTreeView)), None)
        if view is not None:
            # after Picard has restored its saved header (which does not know the new column yet)
            QtCore.QTimer.singleShot(1000, partial(_before_title, view))
    api.plugin_config[OPTION] = True


def _before_title(view, tries=20):
    """Show the new column right before Title (Picard remembers the header from then on)."""
    try:
        header = view.header()
        mine, title = view.columns.pos(KEY), view.columns.pos('title')
    except (KeyError, AttributeError):
        if tries:
            QtCore.QTimer.singleShot(500, partial(_before_title, view, tries - 1))
        return
    if header.count() < len(view.columns):
        # a column registered while the window is open gets no header section until a restart
        view._set_header_labels(update_column_count=True)     # Picard's own (keeps icon columns blank)
    header.show_column(mine, True)
    header.resizeSection(mine, 260)
    header.moveSection(header.visualIndex(mine), header.visualIndex(title))

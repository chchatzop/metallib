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

"""MetalLib branding: this fork must not present itself as "MusicBrainz Picard" nor use its logo
(MetaBrainz trademark). The icon is loaded from plain files next to this module, so Picard's
compiled resources stay untouched (clean upstream merges)."""

import os

from PyQt6 import (
    QtCore,
    QtGui,
)


HOME_URL = 'https://github.com/chchatzop/metallib'
BASED_ON = 'Based on MusicBrainz Picard'
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'metallib_icons')
ICON_SIZES = (16, 24, 32, 48, 128, 256)


def icon_path(size):
    return os.path.join(ICON_DIR, 'metallib-%d.png' % size)


def app_icon():
    icon = QtGui.QIcon()
    for size in ICON_SIZES:
        icon.addFile(icon_path(size), QtCore.QSize(size, size))
    return icon


def logo_pixmap(size=128):
    return QtGui.QPixmap(icon_path(size))

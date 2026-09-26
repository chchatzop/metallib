# MetalLib -- the album cover: the better of Metal Archives' and MusicBrainz' (user, 2026-09-26).
#
# The folder's own image named Front (the Extra files panel) always wins: kept (ticked), it is the
# embedded cover. Without one -- none in the folder, or unticked -- the cover is the best of the
# chosen Metal Archives pressing's cover and the MusicBrainz release's front (Cover Art Archive):
# square first, then bigger, but only up to SIZE_CAP pixels on the short side; beyond that the
# smaller file wins. Discogs' primary image competes too (user). Motivating case: Absurd
# "Werwolfthron" got the MusicBrainz release's 300x297 front while Metal Archives has 1000x1000.
#
# Embedded copies are at most TAG_MAX_SIDE on their longest side; image FILES (the folder's own, and
# a Front file written from a source cover) keep the original, however big (user).
#
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import urllib.request


SIZE_CAP = 1400             # a bigger short side earns nothing more
TAG_MAX_SIDE = 1400         # the copy embedded in the tracks: at most this on its longest side (user)
SQUARE_ENOUGH = 0.9         # short side / long side
CAA = 'https://coverartarchive.org/release/%s/'
USER_AGENT = 'MetalLib/0.1 ( https://github.com/chchatzop/metallib )'


def score(width, height, size):
    """Sort key: higher is better; None for an unreadable image."""
    if not width or not height:
        return None
    square = min(width, height) / max(width, height)
    return (square >= SQUARE_ENOUGH, min(min(width, height), SIZE_CAP), round(square, 2), -size)


def best(candidates):
    """candidates: {source: (width, height, size)} -> the best source, or None."""
    scored = {name: score(*dims) for name, dims in candidates.items()}
    scored = {name: s for name, s in scored.items() if s is not None}
    return max(scored, key=scored.get) if scored else None


def caa_front(release_id, opener=urllib.request.urlopen):
    """Thread: the MusicBrainz release's front from the Cover Art Archive -- the 1200 px version when
    there is one (the original can be a huge scan), else the original. -> (bytes, url) or None."""
    req = urllib.request.Request(CAA % release_id, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with opener(req, timeout=30) as resp:
            listing = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None                             # no cover art (404) or unreachable
    front = next((img for img in listing.get('images') or [] if img.get('front')), None)
    if front is None:
        return None
    thumbs = front.get('thumbnails') or {}
    url = thumbs.get('1200') or front.get('image') or thumbs.get('large')
    if not url:
        return None
    req = urllib.request.Request(url.replace('http://', 'https://'), headers={'User-Agent': USER_AGENT})
    with opener(req, timeout=60) as resp:
        return resp.read(), url


def tag_copy(data, max_side=TAG_MAX_SIDE):
    """The image as embedded in the tracks: scaled down to max_side on its longest side (same format:
    PNG stays PNG, else JPEG), or the data itself when it is small enough or unreadable."""
    from PyQt6 import (
        QtCore,
        QtGui,
    )
    img = QtGui.QImage.fromData(data)
    if img.isNull() or max(img.width(), img.height()) <= max_side:
        return data
    img = img.scaled(max_side, max_side, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                     QtCore.Qt.TransformationMode.SmoothTransformation)
    fmt = 'PNG' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'JPEG'
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt, 92 if fmt == 'JPEG' else -1)
    return bytes(buf.data())


def cover_image(url, data):
    """A Picard front-cover image: the tag copy embedded, the original kept for image files."""
    from picard.coverart.image import CoverArtImage
    image = CoverArtImage(url=url, types=['front'], data=tag_copy(data))
    image.set_external_file_data(data)
    return image


def discogs_front(release, opener=urllib.request.urlopen):
    """Thread: a Discogs release's primary image (else its first). -> (bytes, url) or None."""
    images = release.get('images') or []
    img = next((i for i in images if i.get('type') == 'primary'), images[0] if images else None)
    url = (img or {}).get('uri')
    if not url:
        return None
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with opener(req, timeout=60) as resp:
            return resp.read(), url
    except Exception:
        return None

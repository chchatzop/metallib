# MetalLib -- the album cover: the better of Metal Archives' and MusicBrainz' (user, 2026-09-26).
#
# The folder's own image named Front (the Extra files panel) always wins: kept (ticked), it is the
# embedded cover. Without one -- none in the folder, or unticked -- the cover is the best of the
# chosen Metal Archives pressing's cover and the MusicBrainz release's front (Cover Art Archive):
# square first, then bigger, but only up to SIZE_CAP pixels on the short side; beyond that the
# smaller file wins (it is embedded in every track). Motivating case: Absurd "Werwolfthron" got the
# MusicBrainz release's 300x297 front while Metal Archives has the 1000x1000 one.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import urllib.request


SIZE_CAP = 1500             # a bigger short side earns nothing more
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

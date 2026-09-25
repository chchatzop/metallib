# MetalLib -- the source lookups' own worker threads (audit part 1 M1).
#
# Metal Archives and Discogs are asked one request at a time with a polite pause in between, so a
# queue of lookups (pressing lists fill up to 40 pages per album) can take minutes. On Picard's
# shared thread pool those tasks sat waiting on the client's lock and starved file loading,
# clustering and fingerprinting, and quitting waited for the whole queue. Each source now has one
# thread of its own; a click (a pressing, a lookup) jumps ahead of background filling; quitting
# drops what is queued and stops the running task at its next request.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import threading

from PyQt6 import QtCore

from picard.util import thread


MA = 'Metal Archives'
DISCOGS = 'Discogs'
USER = 1            # started by a click: before any background fill
BACKGROUND = 0

STOP = threading.Event()        # set on quit: clients refuse new requests
_pools = {}


def run(source, func, next_func, priority=BACKGROUND):
    pool = _pools.get(source)
    if pool is None:
        pool = _pools[source] = QtCore.QThreadPool()
        pool.setMaxThreadCount(1)
    thread.run_task(func, next_func, priority=priority, thread_pool=pool)


def shutdown(timeout_ms=15000):
    STOP.set()
    for pool in _pools.values():
        pool.clear()
    for pool in _pools.values():
        pool.waitForDone(timeout_ms)

# MetalLib undo journal -- record a file's on-disk state before a save, put it back on undo.
#
# User rule: anything the program does to the library must be reversible, and the network share
# has no Recycle Bin. So before every save this records:
#   * FLAC  -- every Vorbis comment (order kept) and every embedded picture, byte for byte
#   * MP3   -- the raw ID3v2 tag block and the ID3v1 tag, byte for byte
#   * other -- a full copy of the file (formats rare in the library)
#   * the old path and the names in its folder, so a rename/move and the "additional files"
#     Picard moves along can be reversed.
# Undo never deletes anything: it rewrites tags, and moves files back only onto free paths.
#
# No Picard imports -- unit-testable on its own.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import base64
import json
import os
import shutil
import sqlite3
import threading
import time


BATCH_GAP_S = 30        # saves further apart than this start a new batch


def _long(path):
    if os.name == 'nt' and len(path) >= 250 and not path.startswith('\\\\?\\'):
        return '\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path
    return path


# ------------------------------------------------------------------------------------------------
# Tag snapshots
# ------------------------------------------------------------------------------------------------

def _id3v2_raw(path):
    with open(_long(path), 'rb') as f:
        head = f.read(10)
        if len(head) < 10 or head[:3] != b'ID3':
            return b''
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        footer = 10 if head[5] & 0x10 else 0
        return head + f.read(size + footer)


def _id3v1_raw(path):
    with open(_long(path), 'rb') as f:
        f.seek(0, os.SEEK_END)
        if f.tell() < 128:
            return b''
        f.seek(-128, os.SEEK_END)
        tail = f.read(128)
        return tail if tail[:3] == b'TAG' else b''


def snapshot(path):
    """-> (kind, payload dict) describing the file's current tags."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.flac':
        from mutagen.flac import FLAC
        f = FLAC(_long(path))
        return 'flac', {
            'comments': [list(kv) for kv in f.tags] if f.tags is not None else None,
            'pictures': [base64.b64encode(p.write()).decode('ascii') for p in f.pictures],
        }
    if ext == '.mp3':
        return 'mp3', {'id3v2': base64.b64encode(_id3v2_raw(path)).decode('ascii'),
                       'id3v1': base64.b64encode(_id3v1_raw(path)).decode('ascii')}
    return 'copy', {}


def restore(path, kind, payload, copy_path=None):
    """Write a snapshot back into the file at `path` (audio data is never touched for flac/mp3)."""
    if kind == 'flac':
        from mutagen.flac import (
            FLAC,
            Picture,
        )
        f = FLAC(_long(path))
        if f.tags is None:
            f.add_tags()
        del f.tags[:]
        for k, v in payload['comments'] or []:
            f.tags.append((k, v))
        f.clear_pictures()
        for data in payload['pictures']:
            f.add_picture(Picture(base64.b64decode(data)))
        f.save()
    elif kind == 'mp3':
        from mutagen._util import insert_bytes
        from mutagen.id3 import delete
        v2 = base64.b64decode(payload['id3v2'])
        v1 = base64.b64decode(payload['id3v1'])
        delete(_long(path), delete_v1=True, delete_v2=True)
        with open(_long(path), 'r+b') as f:
            if v2:
                # The exact original ID3v2 block, re-inserted in front of the audio byte for byte.
                insert_bytes(f, len(v2), 0)
                f.seek(0)
                f.write(v2)
            if v1:
                f.seek(0, os.SEEK_END)
                f.write(v1)
    elif kind == 'copy':
        shutil.copy2(copy_path, _long(path))
    else:
        raise ValueError('unknown snapshot kind %r' % kind)


# ------------------------------------------------------------------------------------------------
# Journal
# ------------------------------------------------------------------------------------------------

class UndoJournal:
    def __init__(self, folder, clock=time.time):
        self.folder = folder
        os.makedirs(os.path.join(folder, 'copies'), exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(os.path.join(folder, 'undo.sqlite'), check_same_thread=False)
        self._db.execute('''CREATE TABLE IF NOT EXISTS saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT, batch INTEGER, at REAL, old_path TEXT,
            new_path TEXT, kind TEXT, payload TEXT, copy_path TEXT, dir_listing TEXT,
            state TEXT DEFAULT 'pending')''')
        self._db.commit()

    def close(self):
        self._db.close()

    def _batch(self):
        row = self._db.execute('SELECT batch, at FROM saves ORDER BY id DESC LIMIT 1').fetchone()
        if row and self._clock() - row[1] <= BATCH_GAP_S:
            return row[0]
        return (row[0] + 1) if row else 1

    def record(self, path):
        """Before a save: snapshot `path`. Returns the entry id."""
        kind, payload = snapshot(path)
        folder = os.path.dirname(path)
        try:
            listing = sorted(os.listdir(_long(folder)))
        except OSError:
            listing = []
        with self._lock:
            batch = self._batch()
            cur = self._db.execute(
                'INSERT INTO saves (batch, at, old_path, new_path, kind, payload, dir_listing) '
                'VALUES (?,?,?,?,?,?,?)',
                (batch, self._clock(), path, path, kind, json.dumps(payload), json.dumps(listing)))
            entry = cur.lastrowid
            if kind == 'copy':
                copy_path = os.path.join(self.folder, 'copies', '%d%s' % (entry, os.path.splitext(path)[1]))
                shutil.copy2(_long(path), copy_path)
                self._db.execute('UPDATE saves SET copy_path=? WHERE id=?', (copy_path, entry))
            self._db.commit()
            return entry

    def saved(self, entry, new_path):
        """After a save: where the file ended up."""
        with self._lock:
            self._db.execute("UPDATE saves SET new_path=?, state='saved' WHERE id=?", (new_path, entry))
            self._db.commit()

    def batches(self, limit=20):
        """[(batch, time of last save, [entries])], newest first; only saves not yet undone."""
        rows = self._db.execute(
            "SELECT id, batch, at, old_path, new_path FROM saves WHERE state='saved' ORDER BY id").fetchall()
        by = {}
        for eid, batch, at, old, new in rows:
            by.setdefault(batch, []).append({'id': eid, 'at': at, 'old_path': old, 'new_path': new})
        out = sorted(((b, max(e['at'] for e in es), es) for b, es in by.items()), key=lambda x: -x[1])
        return out[:limit]

    def undo_batch(self, batch):
        """Undo every saved entry of `batch`, newest first. Returns [(entry, ok, message)]."""
        rows = self._db.execute(
            "SELECT id, old_path, new_path, kind, payload, copy_path, dir_listing FROM saves "
            "WHERE batch=? AND state='saved' ORDER BY id DESC", (batch,)).fetchall()
        results = []
        for eid, old, new, kind, payload, copy_path, listing in rows:
            try:
                msg = self._undo_one(old, new, kind, json.loads(payload), copy_path, json.loads(listing))
                with self._lock:
                    self._db.execute("UPDATE saves SET state='undone' WHERE id=?", (eid,))
                    self._db.commit()
                results.append((eid, True, msg))
            except Exception as e:
                results.append((eid, False, '%s: %s' % (os.path.basename(new or old), e)))
        return results

    @staticmethod
    def _undo_one(old, new, kind, payload, copy_path, listing):
        current = new if os.path.exists(_long(new)) else old
        if not os.path.exists(_long(current)):
            raise FileNotFoundError('file is gone (was %s)' % new)
        restore(current, kind, payload, copy_path)
        notes = ['tags restored']
        if os.path.normcase(current) != os.path.normcase(old):
            if os.path.exists(_long(old)):
                raise FileExistsError('tags restored, but %s is taken so the file stays at %s' % (old, current))
            os.makedirs(_long(os.path.dirname(old)), exist_ok=True)
            shutil.move(_long(current), _long(old))
            notes.append('moved back')
            # Companion files Picard moved along (cover.jpg, .cue, ...): back too, if still free.
            new_dir, old_dir = os.path.dirname(current), os.path.dirname(old)
            for name in listing:
                src, dst = os.path.join(new_dir, name), os.path.join(old_dir, name)
                if name != os.path.basename(old) and os.path.isfile(_long(src)) and not os.path.exists(_long(dst)):
                    shutil.move(_long(src), _long(dst))
        return '%s: %s' % (os.path.basename(old), ', '.join(notes))

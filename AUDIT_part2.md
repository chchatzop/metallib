# MetalLib audit, part 2

Scope: the plugins `metallib_folder`, `metallib_tracks`, `metallib_naming`, `metallib_undo`, `metallib_quality` and `metallib_spectral`, as of `metallib` @ `fa94670`. The fork point is `origin/master` (merge base `9de870b`). No code was changed.

I checked every finding below by reading the code path, including the Picard core code it calls. Most were also reproduced with a small script, noted as **Reproduced**. The only Picard behaviour listed is behaviour the stated design does not cover. The design rules given in the brief (trash instead of delete, keep-list tags, placement by title and duration, band country in `releasecountry`, in-memory pressing and provenance data, throttled Metal Archives access) are treated as intended and are not reported.

## Test run

`python -m pytest metallib_plugins/tests test -q` (Linux, offscreen Qt): **6477 passed, 5 failed, 138 skipped.**

- 4 failures are MetalLib tests that hard-code Windows paths (`r'H:\1 New\...'`, `r'C:\in\...'`), so they cannot pass on Linux or macOS. See L7.
  - `test_metallib_folder.py::test_trash_root`
  - `test_metallib_folder_parse.py::TestClusteringUntaggedFiles::test_untagged_scene_files_cluster_under_band_and_album`
  - `test_metallib_ma_album.py::test_pressings_title_names_the_album_folder`
  - `test_metallib_naming.py::CatnumTag::test_catnum_is_the_folder_bracket`
- 1 failure is caused by this sandbox: `test/test_file_identity.py::test_identity_comparison_unreadable_file` fails because the tests run as root, and a `chmod` does not stop root from reading a file.

---

## HIGH

### H1. On Linux and macOS, "move to trash" overwrites earlier trashed files with the same name, and puts the trash in the process's working directory
`metallib_folder/folder_scan.py:160` (`trash_root`), `:213` (`move_to_trash`), called from `extras.py:204` (`execute`) and the Folder contents dialog.

- **What goes wrong:** on POSIX, `os.path.splitdrive()` always returns `''`. So `trash_root()` returns `''`, `rel` becomes just the basename, and `dst` becomes the relative path `.metallib_trash/<batch>/<basename>`.
  - Every file in a batch is moved into one flat folder under whatever the current working directory is.
  - On POSIX, `shutil.move` onto an existing file silently replaces it (`os.rename`).
- **Failing scenario:** a two-disc album has `CD1/rip.log` and `CD2/rip.log` (or two `.cue`/`.nfo` files). The user saves it, and the Extra files step trashes both unticked text files in one batch. The CD1 log is overwritten by the CD2 log and is gone.
  - Undo then fails for the second entry with `No such file or directory`.
  - Undo also depends on the CWD never changing, because the logged `dst` is relative.
  - **Reproduced:** `trash dst .metallib_trash/…/rip.log` (not absolute); the surviving trash content is "log of CD2"; `undo -> [(True,…), (False, "No such file or directory")]`.
- **On Windows** the logged paths include the full relative path and are absolute, and `shutil.move` refuses to overwrite. I checked `\\nas\share\…`, `Y:\…` and `\\?\UNC\…` with `ntpath`, and all three are correct.
- **Fix:** on POSIX, use the mount point (walk up with `os.path.ismount`) or a fixed per-user trash as the root, and always keep the full relative path. Refuse to move onto an existing `dst`, or make `dst` unique as `_free()` does. Also make `trash_root` fail loudly if it cannot produce an absolute root.

---

## MEDIUM

### M1. "Undo a save…" lets the user undo an older save while a newer save of the same file is still in the journal; newer tag changes are silently lost, and a later undo re-applies the undone change
`metallib_undo/__init__.py:90` (picker), `metallib_undo/undo_store.py:176` (`undo_batch`).

- **What goes wrong:** the picker offers any of the last 20 batches, not only the newest. Each entry restores its own snapshot and does not check whether the file was saved again afterwards.
- **Failing scenario:** save a file (batch 1, title "original" → "first save"), then edit and save again (batch 2, → "second save"). Undo batch 1: the title becomes "original" and the batch-2 edit is gone, with no warning. Batch 2 is still listed; undoing it writes "first save" back, which is the change the user just undid.
- **Reproduced:** `undo OLDER batch 1 → title 'original'`, then `undo batch 2 → title 'first save'`.
- A renamed file is safe: its entry fails with "file is gone". The problem is tag-only saves.
- **Fix:**
  - Only allow undoing the newest batch that touches a file, or undo in LIFO order all newer entries for the same paths first.
  - At minimum, refuse an entry whose `new_path` has a later `saved` entry, and say which batch must be undone first.

### M2. The Extra files step (move and rename images, trash text files) silently never runs for an album after a failed or skipped save, or when "Remove complete albums after saving" is on; Picard's own "move additional files" is switched off for album files, so nothing moves them
`metallib_folder/extras_panel.py:490` (`_move_additional_files`), `:496` (`on_file_saving`), `:509` (`on_file_saved`), `:521` (`run_extras`, `files = list(album.iterfiles())` → `return` at `:528`).

- **What goes wrong:**
  - Picard runs file post-save processors only on success (`picard/file.py` `_saving_finished`: the `error is not None` branch and the "file removed" early return both skip `run_file_post_save_processors`). A file whose save failed stays in `st['pending']` forever, so `run_extras` never fires for that album. `SAVE_ATTR` also stays set, so the next save neither re-embeds the Front nor starts a fresh set.
  - With the stock option `remove_complete_albums_after_save`, Picard's own post-save processor (registered first, same priority) removes the album before MetalLib's runs. `run_extras` then finds `album.iterfiles()` empty and returns.
  - In every case the audio has moved to the library, but the covers and booklets stay in the download folder under their old names. The status bar says nothing, and `_move_additional_files` has already stopped Picard's native behaviour for album files.
- **Failing scenarios:**
  1. One track of an album fails to save (for example, locked by a player on a share) and the user removes that track from the album. The remaining files save and move, but the images never follow.
  2. The user has "Remove complete albums after saving" enabled. After any save, the images are left behind.
- **Fix:**
  - Track completion in `File._saving_finished` (already wrapped by metallib_undo) or with a counter that is decremented on success, error and skip alike.
  - Run the extras step for the files that did save, with the album's folders captured at pre-save time. Do not re-read `album.iterfiles()` afterwards.
  - Give the extras step priority over the album removal, or capture `dest` and `prefix` in `on_file_saved` from `file.filename`.
  - If the step is skipped, fall back to the original `_move_additional_files`.

### M3. "Place by fingerprint (AcoustID)" hangs silently if any one file cannot be fingerprinted
`metallib_tracks/__init__.py:238` (`pending = set(files)`), `:245` (`_fingerprinted`), `:249` (`if pending …: return`).

- **What goes wrong:** `AcoustIDClient.analyze` only calls `next_func` after a successful fpcalc run. In `picard/acoustid/__init__.py` `_lookup_fingerprint`, when the fpcalc result is empty (fpcalc not found, crash, or undecodable file) or the file was removed, it logs and returns without calling the callback. The file is then never discarded from `pending`, so `resolve()` never runs for the album.
- **Failing scenario:** an album has 3 unplaced files, and one is a truncated FLAC that fpcalc rejects (or fpcalc is not installed). The status bar stays at "fingerprinting 3 file(s)…", nothing is placed, and there is no error. The two good fingerprints are thrown away.
- **Fix:** do not rely on the callback always arriving. Call `tagger._acoustid.fingerprint`/`_fingerprint` with your own `next_func` that always fires (fpcalc stage), then do the lookup yourself. Alternatively, add a timeout (a `QTimer`) that runs `resolve()` with whatever fingerprints have arrived. Also check `find_fpcalc()` up front, as Spectral does for ffmpeg.

### M4. AAC `.m4a` (and lossy WavPack) files are labelled lossless "16-44", so the album folder gets a lossless quality tag
`metallib_quality/__init__.py:63` (`'lossless': bool(bits)`).

- **What goes wrong:** Picard stores `~bits_per_sample` whenever mutagen reports `info.bits_per_sample` (`picard/file.py` `_info`). mutagen's `MP4Info` sets it from the AudioSampleEntry `sample_size`, which is 16 for AAC as well. So an AAC file has `bits=16` and is treated as lossless: `file_label` → `'16-44'`. `$album_quality()` → folder `… [16-44]`, and library.py then ranks it as lossless (`quality_rank` 100000+), for example "yours is an upgrade" over an existing 320K MP3.
  - Lossy/hybrid WavPack also reports `bits_per_sample`.
- **Reproduced** with Picard's own `test/data/test.m4a` (`mp4a.40.2`): `bits_per_sample 16 → label 16-44`.
- **Fix:** decide lossless by format/codec, not by the presence of bit depth. Treat FLAC/WAV/AIFF/APE/TTA/ALAC as lossless (`~format`, or `info.codec == 'alac'` for MP4) and give AAC/Opus/Vorbis a lossy label or `''`.

### M5. "Undo last clean-up" gets stuck on a batch that cannot be fully undone, so older clean-ups become unreachable; it also undoes whichever album's clean-up was last, not the album the dialog is open for
`metallib_folder/folder_scan.py:184` (`last_batch`), `:188` (`undo`), `metallib_folder/__init__.py:198` (`_undo`).

- **What goes wrong:** `undo()` only marks rows that moved back successfully. A row whose original name is taken again (or whose trash copy is missing) stays not-undone forever. `last_batch()` then keeps returning that same batch, and every click retries it and fails again.
  - The log is global: opening "Folder contents…" for album A and clicking Undo reverts the last clean-up of album B. For example, the automatic Extra files step of the album saved a minute ago moves B's images back to its download folder.
- **Reproduced:** two batches `old` and `new`; recreate `b.nfo` in place; three clicks all give `last_batch new … the original name is taken again`, and `a.nfo` (batch `old`) is never restored.
- **Fix:**
  - Let the user pick a batch, as metallib_undo does, filtered to the dialog's folders.
  - Mark a row as "failed", or skip batches with no movable rows, so older batches stay reachable.
  - Write the log atomically (temp file + `os.replace`). Right now `undo()` rewrites it in place, and a crash mid-write loses the whole undo history, including the paths of everything in `.metallib_trash`.

### M6. The undo journal grows without limit, and snapshots are taken on the UI thread
`metallib_undo/undo_store.py:66` (FLAC pictures, base64), `:138`/`:155` (`record`, full-file copy for non-FLAC/MP3), called from the `File.save` wrapper `metallib_undo/__init__.py:44` on the main thread.

- **What goes wrong:**
  - Every save of a FLAC stores all embedded pictures base64-encoded (+33 %) in SQLite. Every save of any other format copies the whole audio file into `…/metallib/undo/copies/`.
  - Nothing ever prunes `undone`, `pending` or old rows, or the copies.
  - All of this runs synchronously in `File.save`, which Picard calls on the GUI thread in a loop over the selection.
- **Failing scenario:** re-tagging 3,000 FLACs with a 2 MB embedded front cover (common for scans) adds about 8 GB to the SQLite file in AppData on the system drive. Saving a 100-track selection from a share freezes the UI for the whole snapshot phase. A `.wav`/`.dsf`/`.wv` album copies hundreds of MB per save.
- **Fix:**
  - Store each picture once, keyed by hash, in a content-addressed table or directory.
  - Prune by age or count, and let the user clear the journal.
  - Take the snapshot in the save worker: wrap `_save_and_rename`, which runs on `save_thread_pool`, instead of `save`, and keep the "no snapshot → no save" rule by raising there.

---

## LOW

### L1. A partial save (a few tracks of an album) runs the whole-album Extra files step, using the folder and name prefix of an unsaved track
`metallib_folder/extras_panel.py:528`–`531`: `dest = dirname(files[0].filename)`, `prefix_of(files[0].filename)`.

- `files[0]` is the first file of the album, not a file that was saved.
- **Scenario:** the user saves only track 5, which moves to the library. Track 1 is still in the download folder under a name like `Band - Album - 01 - X.flac`. The extras are renamed into the download folder with track 1's prefix, unticked text files go to the trash, a Front file is written there, and the user's ticks and renames are cleared.
- **Fix:** take `dest` and `prefix` from the files saved in this round, and only run once every file of the album lives in the destination folder.

### L2. Blocking file-system I/O on the GUI thread (network shares)
- `$folder()`, `metallib_folder/__init__.py:91`/`:100`: a full `scan()` (listdir, stat, `.sfv` reads) for every album or cluster row the column paints. Its cache is only kept for 60 s, and `_owner()` is O(albums) per cell.
- `ExtrasPanel.refresh`, `extras_panel.py:325`–`336`: `os.walk` of the source folders, plus reading the whole Front image, on every selection and every tick or rename edit.
- `LibraryPanel._poll`/`roots`, `library_panel.py:89`/`:113`: `default_roots()` lists the share's parent directory on the GUI thread whenever the target changes.

On an unreachable SMB share, each of these stalls the UI for the SMB timeout. **Fix:** move them into `thread.run_task` (the library scan already does this) and render when the result arrives.

### L3. The title guessed from a filename drops everything before an inner " N - "
`metallib_tracks/__init__.py:80` (`_TITLE_AFTER_NUMBER_RE`).

- **Reproduced:** `Band - Album - 04 - Part 2 - The End.flac` → `'The End'`. `Symphony No. 5 - …` behaves the same way.
- This only matters for files with no title tag, and there it can leave a track unplaced (it is not a wrong placement).
- **Fix:** prefer the `" - NN - "` track-number token of the library layout, and take the **first** such token after the album part, not the last.

### L4. An album title ending in "..." at the end of a folder name becomes ".._"
`metallib_naming/metallib_layout.txt:15` / `MetalLib.ptsp:24`: `$clean(%album%,keep)`.

- With no type, edition, catalog or quality bracket after the album (for example, unknown quality), the folder name ends in `...`. Picard's `make_save_path(win_compat=True)` then replaces the trailing dot. **Reproduced:** `2001 - Into the Abyss.../` → `2001 - Into the Abyss.._/`.
- **Fix:** use a separate `_album_dir` built with `$clean(%album%)` (dots stripped) for the folder part, and keep `keep` only for the file name.

### L5. Long Windows paths are handled inconsistently
- `folder_scan._long`/`undo_store._long` add `\\?\` only when the path has at least 250 characters. `CreateDirectoryW` without the prefix already fails from 248 characters (Picard's own `WIN_MAX_DIRPATH_LEN = 247`), so `os.makedirs(_long(dir))` fails for 248–249-character folders.
- `extras.list_extras`, `execute` (`os.path.exists(src)`), `_free`, `remove_empty_dirs`, `write_front_file` and `embed_front` use no prefix at all.
- On a system without `LongPathsEnabled`, an extra whose full path exceeds 259 characters is reported as missing by `os.path.exists`, so `execute` silently skips it.
- **Fix:** use one helper everywhere, `picard.util.win_prefix_longpath` or an equivalent, with Picard's thresholds.

### L6. Upstream-merge risk: monkeypatched private Picard methods
- The plugins replace these methods at class level:
  - `File.save` and `File._saving_finished` (undo)
  - `File._move_additional_files` (folder)
  - `Album.match_files`, `File._guess_tracknumber_and_title` and `Cluster.add_files` (tracks)
- Several are private. `_saving_finished` has already changed upstream (commit 1625c567d added batch counters and deferred image updates), and any upstream signature or semantics change breaks these silently.
- `disable()` restores the saved originals unconditionally. If two plugins wrap the same method, disabling them in the wrong order drops the other plugin's wrapper.
- **Fix:** keep a small compatibility test that asserts these signatures (`inspect.signature`) against the vendored Picard. Where possible, move the logic into proper extension points (for example, add a veto-able pre-save hook and a file→track assignment hook upstream).

### L7. The MetalLib tests are Windows-only
Four tests in `metallib_plugins/tests` assert on Windows paths and fail on Linux and macOS (see "Test run"). Upstream's `package-pypi.yml` runs a bare `uv run pytest` on Linux and macOS. **Fix:** mark them `@pytest.mark.skipif(not IS_WIN, …)`, or build expected paths with `os.path.join` / `ntpath`.

---

### Checked and not reported
- **Pre- and post-save processors:** they run on the main thread (`File.save` / `_saving_finished`), so the extras and naming hooks touch Qt objects safely.
- **Undo, spectral and library scans:** they run in worker threads, receive only plain data, and update the UI in callbacks.
- **Windows trash roots:** correct for `Y:\`, `\\server\share` and `\\?\UNC\`.
- **Placement logic (`placement.place`):** contested tracks are never guessed, and ASSUMED is only used for one-to-one leftovers, as designed.
- **MP3/FLAC restore:** round-trips tags byte for byte; APEv2 and ID3-in-FLAC are not snapshotted, but stock Picard does not remove them by default.

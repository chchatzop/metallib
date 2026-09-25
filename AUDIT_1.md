# MetalLib audit — Part 1: Picard core edits + `metallib_plugins/metallib_ma`

**Scope:** `git diff origin/master...metallib` (merge base `9de870b72`), limited to `picard/`, `test/` and
`metallib_plugins/metallib_ma/` (Metal Archives / MusicBrainz / Discogs sources, pressings panel, tag rules).
**Audited commit:** `fa9467075` (tip of `metallib`).
**No code was changed.** Every finding below was checked by reading the whole code path. Where the text says
*Reproduced*, a scratch test or script (shown inline) was run against the audited commit and showed the
failure. I left out findings I couldn't back up, and I did not report any of the documented intended
behaviours (plain tags, the trash folder, placement by title and length, band country in `releasecountry`,
in-memory pressing choices, throttled Metal Archives scraping) as bugs.

## Test run

```
QT_QPA_PLATFORM=offscreen python -m pytest metallib_plugins/tests test -q
5 failed, 6476 passed, 139 skipped, 10971 subtests passed
```

| Failing test | Cause |
|---|---|
| `metallib_plugins/tests/test_metallib_folder_parse.py::TestClusteringUntaggedFiles::test_untagged_scene_files_cluster_under_band_and_album` | Hard-coded Windows path `H:\1 New\...`. On Linux, `pathlib` doesn't split on `\` (see L9) |
| `metallib_plugins/tests/test_metallib_ma_album.py::test_pressings_title_names_the_album_folder` | Hard-coded `C:\m\...` read with `os.path.dirname` on Linux (see L9) |
| `metallib_plugins/tests/test_metallib_folder.py::test_trash_root` | Hard-coded `H:/` (Part 2 plugin, same cause) |
| `metallib_plugins/tests/test_metallib_naming.py::CatnumTag::test_catnum_is_the_folder_bracket` | Windows path (Part 2 plugin, same cause) |
| `test/test_file_identity.py::test_identity_comparison_unreadable_file` | Environment only: the container runs as root, so `chmod 000` doesn't block reads. Upstream test, not fork code |

(Linux needs `libegl1` for PyQt6 `QtGui`, or the whole suite fails at conftest import.)

---

## Summary

| # | Sev. | Where | One line |
|---|---|---|---|
| H1 | **High** | `metallib_ma/__init__.py:766-829` | `apply_rules` silently overwrites the user's hand edits (and tagger-script output) when a late source or pressing arrives |
| H2 | **High** | `metallib_ma/__init__.py:725-732`, `:749-763` | On save, `releasecountry` is always recomputed: the user's own value is thrown away as it's written |
| M1 | Medium | `ma_client.py:205-231`, `pressings_panel.py:40,193-241`, `__init__.py:467,929` | Metal Archives/Discogs scraping blocks Picard's shared thread pool: file loading stalls, and quitting waits for every queued scrape |
| M2 | Medium | `picard/ui/metadatabox/sources.py:57-147` vs `source_columns.py:69,84`, `pressings_panel.py:151,159` | The tag panel's worker thread iterates `source_metadata` dicts while the main thread changes them → `RuntimeError` → blank tag panel |
| M3 | Medium | `ma_release.py:353-366` | `pair_tracks`' same-position shortcut skips the title check where a length is unknown → one track's values land on another |
| M4 | Medium | `__init__.py:280-296, 369-383` | An MA lookup moves the files it saw at the start, not the files still in the cluster, pulling back files the user has since moved or removed |
| M5 | Medium | `picard/__init__.py:48-51`, `setup.py:752-760`, `pyproject.toml:96-97`, `picard.spec` | The rename breaks Linux packaging (missing `.desktop.in`, appdata and icon files), and packaged builds miss the new icons |
| L1 | Low | `__init__.py:509-527`, `pressings_panel.py:92` | After Refresh on a MusicBrainz album, the column shows the auto-picked pressing while the panel highlights the user's |
| L2 | Low | `pressings_panel.py:226-241, 333-344` | Pressing loads race: an older or automatic load can land after the user's click and replace it |
| L3 | Low | `__init__.py:377-387, 555-556` | `AttributeError` (the app shuts down) when a lookup resolves to a restored MA album that is still fetching |
| L4 | Low | `__init__.py:310-353, 995-1021` + `picard/tagger.py:911-922` | A modal picker opened from a thread callback freezes Picard's whole callback queue until it closes |
| L5 | Low | `__init__.py:102-111, 841-851` | Lazy `client()` / `discogs()` singletons aren't thread-safe: two clients, two parallel requests |
| L6 | Low | `__init__.py:1032-1040` | The fallback marks files as tried before the MA lookup runs; a network or Cloudflare failure blocks any retry for the session |
| L7 | Low | `ma_release.py:102-110` + `__init__.py:594-620` | With no durations, any release with the same track count "fits", so its MusicBrainz ids are offered as a confirmed match |
| L8 | Low | several | Upstream-merge risk: monkeypatched private Picard APIs, `sys._getframe`, core hunks |
| L9 | Low | tests | 4 fork tests only pass on Windows, so CI on Linux is red |
| L10 | Low | `picard/ui/metadatabox/__init__.py:1342`, `picard/tagger.py:1676,1882`, `picard/cli/__init__.py:125`, `MANIFEST.toml` | Cosmetic and stock-behaviour changes: movable Picard columns, "MetalLib MetalLib", stale throttle text |

**Qt from background threads:** none found. Every `run_task` worker in `metallib_ma` (`_search`, `_resolve`,
`_background_ma`, `_background_discogs`, `_fill`, `_fetch_ma`, `_fetch_pressing`, `_versions_of`,
`fetch_bytes`) touches only plain data, and all Qt and album work happens in the main-thread callbacks. The
new code that does run in a background thread is `sources.collect/labels/source_tag_names`, called from
`MetadataBox._update_tags` in the priority pool. It touches no Qt objects, but it races on plain dicts (M2).

**Callback exceptions shut the app down.** `tagger.py.in` and `scripts/picard.in` call `register_excepthook()`.
Any uncaught exception in a main-thread callback (for example `_on_resolved`, `_on_discogs` or `_loaded_ma`)
therefore goes through `crash_handler`, which shows a dialog and then calls `os._exit(1)`. Unsaved edits are
lost, which is why L3 counts as a crash.

---

## High

### H1 — `apply_rules` overwrites the user's own New Value edits
**Where:** `metallib_plugins/metallib_ma/__init__.py:766-829` (writes at `:813-816`, `:821-823`; the
protection check at `:777-779`/`:791`). It's triggered by `_on_background_ma.apply` (`:519`), `_use_mb_release.apply`
(`:623`), `_on_discogs` (`:950`), `pressings_panel._apply` (`pressings_panel.py:340`, including the
**automatic** switch from `_filled` at `:240`) and `set_mode` (`pressings_panel.py:160`).

**What goes wrong:** the only thing that protects a tag from the rules is `track.value_sources` /
`file.value_sources`. Those are set only by `sources.use_source_value` (the "Use <source> value" action, or a
double-click on a source cell). A normal edit in the New Value column (`MetadataBox.closeEditor` →
`_set_tag_values`), a paste, "Use original value", a multi-value edit dialog, or a tagger script writes
`file.metadata`/`track.metadata` directly and records nothing. The next `apply_rules` call writes the rule's
source value straight over it with `track.metadata[tag] = values` / `f.metadata[tag] = values`.

**Failing scenario:** the user loads a MusicBrainz album, sees that the catalog number is wrong and types the
right one into New Value. About 20–60 s later the background Metal Archives lookup finishes (`_on_background_ma`
→ `apply_rules`). Or the pressings background fill (`_start_fill`, up to 40 MA pages) finishes and
`better_pick` switches pressing automatically. Or Discogs answers. Any of these puts back the source's
catalog number (MA is first for `catalognumber` in `rules.MA_FIRST`). If the user saves after that, the file
gets the source's value, not theirs. The same happens to `album`, `title`, `label`, `genre`, `date` and every
other tag a source provides. Tagger-script output for those tags is overwritten the same way.

*Reproduced* (scratch test built on the `TestApplyRules` fixture in `test_metallib_sources.py`):
```python
album = self._album_with_both_sources()
t = album.tracks[0]
t.metadata['catalognumber'] = 'MY OWN'      # what a New Value edit does (no value_sources)
self.plugin.apply_rules(album)
assert t.metadata['catalognumber'] == 'MY OWN'
# AssertionError: 'SHVL 804' != 'MY OWN'
```

**Suggested fix:** treat any value in New Value that differs from what the rule last wrote as the user's.
When `apply_rules` writes a tag, keep what it wrote (`track.rule_values[tag] = values`, and the same per
file). On the next run, skip any tag where `list(md.getall(tag)) != rule_values.get(tag)`, and add it to
`value_sources` as `'user'`. A second option is to record `value_sources[tag] = 'user'` for every edit path in
the metadata box (`_set_tag_values`, paste, `_edit_tag`), but that doesn't cover scripts or plugins. The
first approach covers all of them.

### H2 — On save, the user's `releasecountry` is always replaced
**Where:** `on_file_saving` (`__init__.py:749-763`) → `_set_band_country` (`:725-732`) → `_band_country`
(`:708-722`).

**What goes wrong:** unless `releasecountry` is in `value_sources` (only the source-column action sets
that), `_set_band_country` does `md['releasecountry'] = _band_country(md, fallback_md, orig)`.
`_band_country` looks at `~ma_band_country_code`, then `~albumartists_countries`, then the file's
**original** value (`orig`), then falls back to `XU`. The current New Value is never consulted. This runs in
the pre-save processor, so it hits every file saved, including files in clusters and albums that were never
looked up.

**Failing scenario:** a file's original `releasecountry` is `NO` (or it's missing, or Metal Archives has the
band under the wrong country). The user types `SE` into New Value and saves. The file is written with `NO`
(or `XU`), and the panel showed `SE` right up to the save. This is a user edit silently dropped at the moment
of writing.

*Reproduced:*
```python
f = File('/tmp/x.flac')
f.orig_metadata = Metadata(title='a', releasecountry='NO')
f.metadata = Metadata(title='a', releasecountry='SE')   # user's correction
plugin.on_file_saving(MagicMock(), f)
assert f.metadata['releasecountry'] == 'SE'
# AssertionError: 'NO' != 'SE'
```

**Suggested fix:** in `_set_band_country`, leave `md['releasecountry']` alone when it already differs from
`orig['releasecountry']`, i.e. the user or a script changed it, and apply the same rule as H1. Only fill it
in when New Value still holds the original or nothing. Better still, compute the band country once in
`apply_rules`/`_tidy_track` where it's visible in the panel, and don't recompute it at save.

---

## Medium

### M1 — Scraping blocks Picard's shared thread pool (slow file loading, slow quit)
**Where:** `ma_client.py:205-231` (`MAClient.fetch` holds `self._lock` through `self._sleep(wait)` and a
network request of up to 30 s, `TIMEOUT = 30`). Every lookup goes through `thread.run_task(...)` without a
`thread_pool` argument, so it runs on `tagger.thread_pool` (`picard/util/thread.py:153-155`), which is
`max(3, idealThreadCount())` threads shared with `File.load` (`picard/file.py:351`), clustering and
fingerprinting. Call sites: `__init__.py:160,306,328,391,467,929,1109,1111`;
`pressings_panel.py:203,249,252`. `_fill` (`pressings_panel.py:208-223`) fetches up to
`FILL_CAPS['Metal Archives'] = 40` pages in **one** task, and it starts for every album that gets an MA list:
both MA albums and every MusicBrainz album via `on_mb_album` (`:459`) → `_record_ma`.

**What goes wrong:** there's one global lock, so only one task makes progress. Every other MA or Discogs
task sits in a pool thread waiting on the lock. With about as many albums as pool threads, all threads are
waiting and `File.load` / clustering / AcoustID tasks queue behind them. Picard also calls
`thread_pool.waitForDone` at exit (`tagger.py:318`), which waits for every queued scrape, possibly hundreds
of pages at about 0.7 s each.

**Failing scenario:** the user drops 10 already-tagged MusicBrainz albums into MetalLib, then adds a new
folder of files. Every album starts `_background_ma` (search + album + versions + up to 4 pressings + band)
followed by a 40-page `_fill`. The new files stay "pending" for tens of seconds or minutes. Closing the
window then hangs until the scrape queue drains.

*Reproduced* with the real `MAClient`, a fake session that takes 0.3 s per request, a 3-thread
`QThreadPool` (Picard's minimum), 6 fill tasks of 10 pages each, then one priority-1 task (what `File.load`
uses):
```
file-load task started after 22.1 s (MA tasks total 59.4 s)
```
Script: `pool_starve.py`, kept in the session scratchpad, not committed.

**Suggested fix:** give MetalLib its own `QThreadPool(maxThreadCount=1)` for MA and another for Discogs
(`run_task(..., thread_pool=_ma_pool)`). The lock then serialises nothing extra and Picard's pool stays
free. Split `_fill` into one task per page so it can be cancelled. On quit (`tagger.stopping`, or a
`register_cleanup` hook), call `pool.clear()` before `waitForDone`, and check `tagger.stopping` between
pages. Consider limiting the automatic 40-page fill to the album currently selected.

### M2 — Tag panel worker races with source-column changes on the main thread
**Where:** `picard/ui/metadatabox/__init__.py:1105-1110` runs
`sources.source_tag_names/collect/labels` inside `_update_tags`, which runs on
`tagger.priority_thread_pool` (`:1064-1068`). These iterate each object's `source_metadata` dict
(`sources.py:63, 118, 146`) and the `Metadata` objects in it. At the same time the main thread changes those
dicts in place: `source_columns.attach` `del sources[source]` (`:69`), `set_own_source`
`sources[source] = md` (`:84`), `pressings_panel.set_mode` `del sources[source]` / `sources[source] = md`
(`:151,159`), and `_label_column` `md[LABEL_TAG] = label` (`pressings_panel.py:183`).

**What goes wrong:** `RuntimeError: dictionary changed size during iteration` in the worker. `Runnable.run`
turns that into `_update_items(error=...)`, which sets `self.tag_diff = None` and clears the tag panel until
the next update. There's also a traceback in the log.

**Failing scenario:** an album is selected while background MB, MA and Discogs results arrive or a pressing
is clicked. `attach()` runs in the same moments that `_refresh_panel()` → `box.update()` starts a worker. The
panel flashes empty, and if nothing else triggers an update it stays empty.

*Reproduced* (a reader thread running `collect`/`labels` while the main thread does what `attach()` does):
```
AssertionError: 7 RuntimeErrors, e.g. dictionary changed size during iteration
```

**Suggested fix:** never change a published `source_metadata` dict in place. Build a new dict and assign it
(`track.source_metadata = new`), which is atomic under the GIL, and treat the `Metadata` values as immutable
(copy before setting `~source_label`). In `sources.py`, also copy before iterating
(`list(object_sources(obj).items())`).

### M3 — `pair_tracks` pairs by position even when titles contradict and a length is unknown
**Where:** `metallib_plugins/metallib_ma/ma_release.py:353-366`.

**What goes wrong:** the "same track count, confirmed in order" shortcut only checks the title when
**both** lengths are known and more than 5 s apart (`elif d is not None and title_score(...) < PAIR_TITLE:
break`). When either length is unknown (`d is None`), which is common on Metal Archives pages and older MB
releases, the position is accepted with no check at all. The shortcut then only needs half the positions to
be confirmed by length.

**Failing scenario:** an album's tracks 1–2 have matching lengths, and tracks 3–4 have no length in one
source and appear in a different order (for example a vinyl running order). They're paired by position, so
the track "Winter" gets the MA column of "Outro". `apply_rules` then writes that column's `title` (MA is
first for `title`) into the "Winter" file, or the MB column's `musicbrainz_recordingid`/`isrc` for the MB
direction. That's a wrong tag written on save.

*Reproduced:*
```python
targets = [t('Intro',60,1), t('Storm',300,2), t('Winter',0,3), t('Outro',0,4)]
sources = [t('Intro',61,1), t('Storm',301,2), t('Outro',95,3), t('Winter',420,4)]
pair_tracks(targets, sources)   # -> {0: 0, 1: 1, 2: 2, 3: 3}   (Winter<->Outro swapped)
```

**Suggested fix:** in the shortcut, a position with an unknown length must be confirmed by its title:
`elif title_score(t, s) < PAIR_TITLE: break` when `d is None` too. Untitled tracks could still count as
neutral. Add the case above as a regression test.

### M4 — An MA lookup moves the files it saw at the start
**Where:** `_local_info` (`__init__.py:280-296`) captures `files = list(cluster.iterfiles())`.
`_on_search` / `_on_resolved` (`:310-353`) and `_build_album` (`:369-383`) call
`tagger.move_files_to_album(local['files'], album=album)` seconds or minutes later, after the search, up to
12 pressing fetches, and one or two modal pickers. Nothing checks that the cluster still exists or that each
file is still in it and still loaded. For comparison, upstream `Cluster._lookup_finished` moves the files
that are in the cluster at the moment the callback runs (`self.files`, `picard/cluster.py:407`).

**What goes wrong:** `Album.match_files` → `File.move` (`picard/file.py:907-925`) moves any file whose
`parent_item` differs, with no check of `File.State`.

**Failing scenario:** the user runs "Load from Metal Archives" on a cluster (or "Lookup" falls through to it
via `_cluster_lookup_finished`). While the lookup runs, they drag three of those files onto a MusicBrainz
album, or remove them from MetalLib. When MA answers, those files are pulled back into the MA album; a
removed file is re-added as a zombie that isn't in `tagger.files`. A later save then writes MA tags to files
the user deliberately put elsewhere.

**Suggested fix:** in `_build_album`, re-read the files at completion:
`files = [f for f in local['files'] if f.state != File.State.REMOVED and f.parent_item is cluster]`
(or simply `list(cluster.iterfiles())` when `cluster in tagger.clusters`), and do nothing if the cluster has
gone. Check the same in `_on_search`/`_on_resolved` before opening a picker.

### M5 — Renaming the app id breaks packaging, and the new icons aren't shipped
**Where:** `picard/__init__.py:48-51` changes `PICARD_APP_ID` to `io.github.chchatzop.MetalLib`.
`setup.py:82-85, 752-760` derive file names from it: `io.github.chchatzop.MetalLib.desktop.in`,
`.appdata.xml.in`, `resources/io.github.chchatzop.MetalLib.svg` and
`resources/images/{size}x{size}/io.github.chchatzop.MetalLib.png`. **None of these exist.** The repo still
has only `org.musicbrainz.Picard.*`. Separately, `picard/metallib_brand.py` loads PNGs from
`picard/metallib_icons/`, but `pyproject.toml:96-97` only ships `*.mo` as package data and `picard.spec`'s
`data_files` has only locale files.

**Failing scenario:** `python setup.py build` on Linux runs `build_appdata` and `build_desktop_file`
(`setup.py:188-190`), which fail on the missing templates, and `install` fails on the missing `data_files`.
In a wheel or PyInstaller build, `app_icon()` / `logo_pixmap()` load non-existent files, so the window,
taskbar and About dialog show no icon. The dev checkout works, which hides this.

**Suggested fix:** add the renamed `.desktop.in`, `.appdata.xml.in`, SVG and PNG resources, or keep them
under the old names with a MetalLib-specific constant for packaging. Add `"picard" = ["metallib_icons/*.png"]`
to `[tool.setuptools.package-data]` and the icon folder to the PyInstaller `datas`.

---

## Low

### L1 — After Refresh, the column and the highlighted pressing disagree
**Where:** `pressings_panel.state()` lives on the `Album` object and survives `Album.load(refresh=True)`
(`picard/tagger.py:1610-1613`). The refresh runs `on_mb_album` again (`__init__.py:459-474`) →
`_on_background_ma.apply` (`:519-527`), which only checks `blocked()` (special rows) and then
`attach()`es the **automatically** chosen MA pressing. `_record_ma` → `record()` keeps the user's `chosen`
because `user_picked` is set (`pressings_panel.py:92`). `_on_discogs` (`:934-955`) does the same.

**Failing scenario:** on a MusicBrainz album the user clicks MA pressing B and then presses Refresh. The MA
column (and New Value, via `apply_rules`) now shows the auto pick A, while the panel still highlights B.

**Fix:** in those `apply()` paths, when `st.get('user_picked')`, load `st['chosen']` (via
`pressings_panel.load`) instead of the automatic result, or skip `attach` altogether.

### L2 — Pressing loads can finish out of order
**Where:** `pressings_panel._apply` (`:333-344`) applies whatever finishes, then `set_chosen(cid)`. There's
no check that `cid` is still the pressing that was last asked for. `_filled` (`:226-241`) can start an
automatic `load(better)` just before the user clicks, and `threading.Lock` in the MA and Discogs clients
isn't FIFO, so the order of completion isn't guaranteed.

**Failing scenario:** the background fill picks "better" pressing P and starts loading it. The user clicks Q
(already cached). Q finishes first and P finishes second, so the column and New Value end up on P, which the
user didn't choose.

**Fix:** store `st['wanted'] = cid` in `load()`. In `_loaded_*`/`_apply`, drop any result whose `cid` isn't
`st['wanted']`. In `_apply`, also drop automatic results once `user_picked` is set.

### L3 — Crash when a lookup resolves to a restored MA album that is still fetching
**Where:** `_build_album` (`__init__.py:375-387`) reuses `tagger.albums.get(aid)`. For an album restored from
a session without data (`restore_album` `:1095`, `_ma_node = None`, still inside `_fetch_pressing`),
`album.tracks` is empty, so `start_mb_lookup` (`:556`) evaluates `album._ma_node.get('media')` →
`AttributeError: 'NoneType' object has no attribute 'get'` in a main-thread callback. The excepthook then
shuts the app down. The same path also starts a second `_fetch_pressing`.

**Fix:** in `start_mb_lookup`, use `(album._ma_node or {}).get('media')`, and when the album isn't loaded
yet, run the MB lookup through `_when_loaded(album, ...)`.

### L4 — A modal picker opened from a thread callback freezes Picard's callback queue
**Where:** `_on_search` / `_on_resolved` (`__init__.py:310-353`) run inside
`Tagger._process_callback_batch` (`picard/tagger.py:911-922`) and call `_pick` → `dialog.exec()`
(`:1019`). While the nested event loop runs, `_callback_timer_running` stays `True`, so new
`ProxyToMainEvent`s are only queued (`tagger.py:894-899`). No file-load, save, clustering or tag-panel
completion is processed until the dialog closes. This path is reached without the user asking for it through
the Lookup fallback (`_cluster_lookup_finished`).

**Fix:** open the picker outside the batch, with `QtCore.QTimer.singleShot(0, partial(_ask_and_continue, ...))`,
and use `dialog.open()` with a `finished` slot instead of `exec()`.

### L5 — Lazy singletons created from several threads
**Where:** `client()` (`__init__.py:102-111`) is first called from pool threads (`_search`,
`_background_ma`, `_fetch_pressing`); `discogs()` (`:841-851`) likewise. Several tasks starting together,
for example several MusicBrainz albums restored from a session, can each create an `MAClient`. For that
moment the one-at-a-time guarantee is broken: each client has its own lock, so two requests go to Metal
Archives in parallel, and two sqlite connections write the same cache. Writes can then fail with an
`sqlite3.OperationalError: database is locked` that isn't an `MAError`, so the lookup fails.

**Fix:** create both clients in `enable()` on the main thread, or guard creation with a module-level `threading.Lock`.

### L6 — A failed MA fallback is never retried
**Where:** `fallback_lookup` (`__init__.py:1032-1040`) sets `metallib_ma_fallback_tried` on every file
**before** `start_lookup`. If the MA search then fails (network, Cloudflare challenge `MAError`), `_on_search`
only updates the status bar. Every later Lookup of those files skips Metal Archives until restart.

**Fix:** set the flag only after a successful search, or clear it in `_on_search`/`_on_resolved` when there's
an error.

### L7 — Without durations, any release with the right track count counts as fitted
**Where:** `fits()` (`ma_release.py:102-110`) returns `True` on track count alone when either side has no
durations. `_on_mb_release` (`__init__.py:594-611`) passes that result to `_use_mb_release(fitted=True)`,
which keeps the MusicBrainz ids (`:614-620` only strips them when `fitted` is false). `apply_rules` then
writes `musicbrainz_albumid`/`musicbrainz_trackid` (MB is first for `musicbrainz_*`).

**Failing scenario:** an MA demo or EP with no track times whose MusicBrainz search returns a different
10-track release with the same count. That release's ids are written as a confirmed match, which ties the
files to the wrong MusicBrainz release.

**Fix:** use `judge()` here, which already returns `NO_LENGTHS`, and treat `NO_LENGTHS` as not fitted for the
purpose of offering ids.

### L8 — Upstream-merge conflict risk
- **Silent-breakage monkeypatches** (setting an attribute never fails, so an upstream rename just turns the
  feature off without any error): `picard.cluster.Cluster._lookup_finished` (`__init__.py:1223`),
  `session_loader.AlbumManager.load_album_with_strategy` / `_build_from_cache` (`:1170-1173`),
  `tagger.load_album` / `tagger.move_file_to_nat` (`:1174-1177`) and `picard.cluster.album_artist_from_path`
  (`:1219-1220`). **`_move_file_to_nat` checks `sys._getframe(1).f_code.co_name == '_file_loaded'`
  (`:1159`)**, so if upstream renames or wraps `Tagger._file_loaded`, the check quietly stops matching. The
  code also depends on private `Album` internals (`_parse_release`, `_finalize_loading`, `_new_metadata`,
  `_new_tracks`, `_pending_tasks`, `_release_artist_nodes`, `picard.album._copy_artist_nodes`,
  `release_group_to_metadata`) in `MetalArchivesAlbum.load` and `source_columns.ShadowAlbum`/`track_metadata`.
  Add a startup self-check (`hasattr` + a signature check) that logs loudly when a patch target is missing.
- **Core hunks in upstream-active files:** `picard/ui/metadatabox/__init__.py` (7 hunks across
  `get_selected_tags`, `_copy_single_item`, the context menu, `_update_tags`, `_update_items`,
  `restore_state`/`save_state`), the `TagDiff.__slots__` line (`tagdiff.py:219`), `update_tag_names`,
  `_RELEASE_TO_METADATA`, `USER_AGENT_STRING` and the `mainwindow` session-filter strings. The new msgid
  `"MetalLib Session (%s);;All files (*)"` has no translations, and the window title is no longer translated.
  Keeping the hunks one line each (hooks into `sources.py`), as most of them already are, is the right
  direction. The `restore_state`/`save_state` and `_fill_source_cells` bodies could move into `sources.py`
  behind one-line calls.

### L9 — Four fork tests only pass on Windows
`test_metallib_folder_parse.py:81`, `test_metallib_ma_album.py:459-469`, plus two Part 2 tests, use
`H:\…`/`C:\…` paths parsed with `pathlib.Path`/`os.path` (native semantics), so they fail on Linux or macOS
CI. **Fix:** use `pathlib.PureWindowsPath` / `ntpath` explicitly in those tests, or mark them
`@unittest.skipUnless(IS_WIN, ...)`.

### L10 — Cosmetic and stock-behaviour changes
- `restore_state` calls `header.setSectionsMovable(True)` (`metadatabox/__init__.py:1342`), which makes
  Picard's own Tag / Original / New columns draggable, and the new order persists in
  `metadatabox_header_state`. If only the source columns should move, reset the three stock columns to their
  visual positions in `_apply_source_layout`.
- `f"{PICARD_ORG_NAME} {PICARD_APP_NAME}"` now reads **"MetalLib MetalLib"**: `--version`
  (`picard/cli/__init__.py:125`, `picard/tagger.py:1882`) and a message box title (`tagger.py:1676`).
- `metallib_ma/MANIFEST.toml` says requests are "1-1.5 s apart", but `ma_client.py:25` uses 0.6–0.8 s.

---

## Checked and found OK (not reported)
- `mbjson` `packaging` mapping: `_node_skip_empty_iter` skips `null`, so no `"None"` values.
- `sources._fill_source_cells` `~length`: `TagCounter.__getitem__` returns `[""]`, so there's no
  `IndexError`.
- UNC and `\\?\UNC\` paths in `folder_parse.folder_hints` (tested with `PureWindowsPath`): no crash, and
  sensible hints.
- The single-instance pipe name, config and plugin folders follow `PICARD_APP_NAME`, so MetalLib and a stock
  Picard don't collide.
- Clustering with the patched `album_artist_from_path` runs in the thread pool (`tagger.py:1566`); the
  directory listing is not on the UI thread.

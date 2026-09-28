# iPod lifecycle consolidation — September 26, 2026

Implementation audit of the existing dirty checkout (base bb0a202). This work
must preserve generation-58 recovery and the current 202-entry Favorites.
Hardware acceptance is outstanding; automated fixtures are not that acceptance.

## Inventory before consolidation

Identity abbreviations: P = mount path, T = root st_dev mount token,
S = engine DeviceSession pinned filesystem identity, L = stock library ID +
volume UUID. Firmware and protected-path guards remain in the existing engines.
Workers remain owned until QThread.finished, not their result signals.

| Entrypoint / implementation | Identity / owner | Reads and writes / confirmation | Queue and stop boundary | Persistence / terminal / existing coverage |
|---|---|---|---|---|
| Connection: app._refresh_devices, DeviceScanWorker | P,T / _dev_scan_worker | Enumerate, profile, bounded drift; read-only | Single flight; discovery emits before thread finishes | Mount/profile caches; rows publish after finished; test_ui discovery/finished tests |
| Dashboard: _read_stock_device_view | P,T,L / _stock_view_worker | iTunesDB/artwork/playlist metadata; read-only | Pending dashboard list; serialized against poll | Immutable cached snapshot; failed reads remain hidden; test_ui preload tests |
| Drift: _poll_devices | P,T / scan worker | Manifest/source stat/device sizes; read-only | Timer suppressed by export/delete/dashboard flags | _drift_cache, Sync-needed label; test_ui poll tests, test_manifest |
| Playlist drop: _on_device_drop | P / export or dual workers; S in engine | Confirmed selected playlist add/reconcile, layout routes engine | Generic contiguous-target batches; dual current + next confirmed batch | Schema 2 or 3 manifest, stock shadow/recovery; test_export_batching, test_dual_ipod |
| Sync: _sync_device | P,L / resync, stock, or dual workers; S | Confirmation before planning; reconcile managed set | Read-only plan then commit; generic cancel between files | Stock/composite recovery + publication order; test_stock_ipod, test_dual_ipod, test_sync |
| Stock playlist deletion | P,L / separate plan and commit workers; S | Exact selected IDs, protected smart indexes; confirmation | Independent destructive intent; no general cooperative stop | Recovery-backed DB, reference-counted cleanup; test_stock_ipod, test_ui |
| Browser rename/assign/new-folder | P,S / synchronous window method | Guarded external paths; input dialog is confirmation | Defers for poll; synchronous mutation after revalidation | No transaction journal; explicit errors; browser UI tests |
| Browser delete/erase | P,S (erase creates S in worker) / _delete_worker | Exact paths or separately confirmed volume erase | Delete stops between entries; erase OneShot has no stop | Delete partial result; erase completion may eject; test_sync, test_ui |
| Browser import | P / _device_import_worker | Read device, write SoundBox-owned imports; explicit action | OneShot; no cancellation | Imported files/register results; test_imports |
| Verify / Deep Verify | P / _device_verify_worker | Read-only structural / content verification | OneShot; busy guards | UI report, no repair authority; verification tests |
| Cancel | export worker only in button handler | No independent writes | Generic stop flag; queued jobs remain allocated; other engines lack token | Partial generic report; cancellation tests in test_sync |
| Quit | all worker attributes / window.shutdown | Save UI settings, stop player | Timed waits (6–30 seconds), no guarantee workers stopped | Risk of live QThread teardown; no full asynchronous close test |
| Recovery | S + manifest/database hashes / engine adapters | Exact baseline-bound restores, interrupted-file adoption | Inline rollback; disconnected recovery must fail closed | Dual journal can remain `copying` after successful rollback; engine injection tests |
| Finalization | P / dashboard then sidebar workers | Read affected views, no transaction writes | Serial refresh, poll hold through dialog | Refresh error distinct in UI; test_ui finalization tests |
| Eject | P / _eject_verify_worker then QProcess | os.sync + mandatory verify_export, then diskutil | Poll may finish first; writers refused | Missing media blocks eject; offers force-unmount; existing UI tests cover poll/writer guard |

## Observed defects

- Mandatory eject verification traps an idle, damaged library behind repair.
- Timed waits do not establish a safe shutdown boundary.
- Materialization validates cloud timestamps then repeatedly reopens the source
  for inspection, hashes, copying and essence verification. The build-108
  CRANKDAT failure occurred after cache eviction changed ctime.
- Generic export retains every prepared derivative before copying begins.
- Generic export catches a failed batch and continues queued work; cleanup runs
  after cancelled/failed batches. This violates stop-on-failure intent.
- Dual rollback does not publish an explicit rolled-back journal outcome.

## Risks requiring tests (not observed hardware defects)

- Path-only deferred commands can outlive a mount and target a replacement.
- Separate busy flags can miss ownership during modal event loops.
- Source changes with preserved mtime require exact byte proof; stat is not
  sufficient once a planned source must be acquired.
- Cancellation after publication must not claim rollback; refresh failure must
  not change a committed result.

## Failure matrix and acceptance

Fixtures must cover acquisition timestamp drift/eviction, differing same-size
bytes, authentication/read failure, bounded unstable retry, local space failure,
canonical-source access forbidden after capture, destination verification,
rollback failure, cancellation before/after publication, stale callbacks,
remount authority, batching and idle eject with known missing media.
Measure canonical bytes/acquisitions, preparation counts, receipt hits, device
writes and peak staging. Normal unchanged composite Sync must read zero source
audio and write zero media. Audio staging must remain one source + derivative.

Required physical gates after full source checks: normal SeaDrive-managed Sync,
unchanged Sync, overlapping drops, cancel/retry, idle eject after success and
failure, reconnect. No live transaction, forced eject, SeaDrive setting change,
commit or push is authorized implicitly by this audit.

## Implementation and current acceptance

The coordinator now gates discovery, dashboard reads, planning/commit, browser
mutations, verification, and normal eject. Engines retain their firmware guards.
Adapter queues still retain GUI-specific batching state; the coordinator's
command queue is tested independently, so complete removal of duplicate queue
state remains an integration limitation. Source acquisition and compact receipts
serve stock/composite/filesystem payloads; generic execution releases each track
before acquiring the next. Outcomes distinguish rollback, recovery required,
partial filesystem completion, and committed data with refresh failure.

Bare checks passed before installation (913 tests, four skipped). The signed
lifecycle build was installed and launched at 16:14 on September 26; curated
library, all 202 Favorites, 3298 artwork hashes and 2924 analysis baselines were
verified. Sonny started hardware Sync at 16:15:22. It passed CRANKDAT acquisition
with one hydration retry; this is not evidence of completed commit.

The supplied 16:07:40 offscreen Python crash exposed an observed artwork lifetime
bug: an old finished callback could delete a new ArtFillWorker. The source now
retains the slot through finished delivery, retires the captured instance, and
suppresses artwork restarts during shutdown. Three deterministic regressions
cover these boundaries. Reinstallation must wait for the active hardware run.

The 16:15 hardware run failed at 16:24:48 during stock compatibility planning,
after media verification (2577 hits / 164 proof reads / 20.10 GB). A remaining
stat-only check rejected AD MIX HOSTED BY SKULLION SHADEZ. No commit started.
The fix removes this redundant compatibility check and the second global
preflight stat sweep: stable per-source byte proofs remain valid after later
cache eviction, and execution still requires the exact acquired digest. Added
regressions cover both windows. Hardware acceptance remains outstanding.

## Offline capture findings and regressions

September 26 metadata inventory (generation 58) shows two absent Rockbox-only
WAVs and their ten per-track BMPs. The previous planner rejected missing owned
media and could retain missing cover records. It now plans guarded verified
copies for absent owned paths, rechecks vacancy at commit, and keeps unrelated
occupants untouched. The Favorites M3U8 retains its exact historical hash prefix
with nine appended paths; the capture preserves this evidence for reconciliation.

The real 1839-track iTunesDB exposed a separate no-cache database rewrite:
Clickwheel regenerated opaque headers even with no semantic mutation. Full
reconciliation now preserves original bytes when media, metadata, playlists,
artwork and playback merge all remain unchanged. An artwork reconciliation that
actually schedules publication is still a change; this does not bypass it.

Opt-in `tests/test_ipod_capture.py` uses writable disposable metadata copies,
sparse historical-media size fixtures that reject audio reads, and generated
WAV/AAC audio. It tests cloud timestamp eviction, canonical reads forbidden after
materialization, rollback after stock publication, failure at manifest publication,
retry, cancellation after commit, unchanged follow-up and repair of two tracks plus
ten covers. Acquisition metrics assert one canonical capture per generated track
and staging bounded to one source plus derivative. All 4854 pristine captured
files were re-hashed after tests with no changes. This is not a full 2881-source
hardware Sync or historical-media integrity test.

## Generation 59 hardware result

The user-operated run committed generation 59 at 18:26:26 and refreshed its
views. Commit metrics: 590256273 device bytes written, 21 acquisitions and
transformations, peak audio staging 116279298 bytes. Commit/preflight read
20885090143 canonical bytes (166 source-proof reads); this remains expensive.
The postcommit Sync-needed badge was reproducible from 83 ctime-only source
changes with unchanged size/mtime. Polling now ignores ctime alone while strict
proof invalidation and acquisition remain unchanged. Regression tests exercise
ctime-only metadata change and mtime drift. No automatic hardware resync occurred.

## September 27 reconnect correction

User reported two mounts visible in Finder but absent from SoundBox. The live
mount enumerated correctly in an independent bounded probe. Code inspection
found post-sync eject-ready and playback gates suppressing mount notifications,
plus an explicit sidebar visibility gate waiting for dashboard+drift completion.
Mount events now request a forced scan; busy-lane deferrals retain pending intent.
A three-second local /Volumes timestamp fallback catches missed events without
opening child volumes. Recognized rows appear disabled during discovery, then
selectable/loading while the serialized dashboard preload finishes. Regular
polling no longer stops for playback. No playback stop/pause was added to device
management. Tests cover hold/playback bypass, busy retry, missed events and row
readiness. A synthetic unchanged reconnect with Rockbox cache/preferences and
source ctime updates remains clean; a real title edit then triggers drift.
Physical repeated reconnect while playback runs remains an acceptance check.

September 27: the actual reconnected generation-60 device had no managed library
or playlist drift, only the absent unsupported `sort playlists` setting. Rockbox
4.0's [settings table](https://raw.githubusercontent.com/Rockbox/rockbox/v4.0-final/apps/settings_list.c)
does not define that key. SoundBox no longer inserts or requires it. This corrects
the prior documented assumption; no device configuration was rewritten for the fix.

# SoundBox current handoff

Updated September 27, 2026, America/Chicago.

## Checkout and runtime

Local `main`; Sonny explicitly requested complete wrap-up on September 27.
The release includes the previously dirty playlist/performance work and lifecycle
corrections. Resolve the release revision with `git rev-parse HEAD`; the final
clean build records it in Info.plist. Commit/push are authorized for this release.
Reconnect/playback/firmware-config correction installed at `/Applications/Soundbox.app`
with signature verification; relaunched September 27 at 20:01:07. Native
CoreAudio/Metal active. Discovery found the mounted iPod in 1.27 seconds and the
loading row was visually confirmed before dashboard completion at 20:01:22.
Loaded 3397 tracks / 75 playlists, zero preparation work and zero art fetched.
Postlaunch library hash, 202 Favorites, 3298 art hashes and 2924 analysis baselines
match the closed state. Generation-60 manifest hash is unchanged. Previous app is
`lifecycle-before-reconnect-fix.app` in the existing rollback directory.

## Current correction

Sonny reported two reconnects visible in Finder but absent in SoundBox. The live
`/Volumes/IPOD` enumerated correctly with a bounded independent probe. Discovery
was suppressed by an indefinite post-sync eject hold and playback; healthy iPod
rows were also deliberately hidden until dashboard+drift preload finished.

- Mount notifications request discovery despite idle/eject holds; pending intent
  survives active workers. A three-second local /Volumes timestamp fallback
  catches missed events without opening children for the timestamp check.
- Recognized rows appear disabled during discovery, become selectable at
  QThread.finished, and show library loading while serialized preload proceeds.
  Remounts invalidate old views; operations still use coordinator/engine guards.
- Playback no longer suppresses discovery or ordinary device polling. No device
  action stops/pauses the player; only app shutdown does. Heavy device work
  remains serialized. Physical playback/reconnect acceptance remains pending.
- Post-sync poll holds last through the completion dialog, then release so real
  subsequent SoundBox edits update the badge without requiring reconnect.
- Sonny's false-Sync suspicion was reproduced on the actual generation-60 iPod:
  only `rockbox-settings` drift, no playlist/library changes. The firmware-saved
  config lacks `sort playlists`; Rockbox 4.0's official settings table has no
  such key. SoundBox no longer adds/requires it; existing unknown lines remain
  untouched. Prior docs claiming support were corrected in both runtime files.
- With the correction, the actual mounted device compares clean against the
  current cached library, imports, curation, artist registry and Collection order.
  Synthetic reconnect/Rockbox cache/config saves/source-ctime updates stay clean;
  a real title change then triggers drift. This is not a content-integrity proof.

## Hardware state and outstanding gates

The iPod is disconnected at wrap-up (no IPOD mount). Last observed manifest
generation 60, SHA-256
`63844c8b231d77d7794a42252ebe038b1c2a46f624cab0599eea944e4ef7fdd5`.
No agent-initiated device write, eject or Sync occurred in this correction.
Sonny operates hardware checks. Observe repeated eject/reconnect/power-cycle
with music playing, clean badge with no edits, and real curation drift afterward.

Generation 59 committed at 18:26:26 September 26; 590256273 bytes written,
21 acquisitions/transforms, 116279298 peak staging bytes. Preflight/commit read
20885090143 canonical bytes / 166 proof reads: performance remains expensive.
Generation 60 was user-operated; screenshot shows zero media/cover/playlist copies.
Do not infer zero source reads or zero database/manifest writes from that dialog.
Other remaining gates: unchanged follow-up zero source-audio reads/media writes,
overlapping export, cancel/retry, eject after failure, repeated reconnect.
Do not call the entire lifecycle correction complete.

The coordinator serializes heavy I/O through QThread.finished, rejects stale
callbacks and preserves cooperative stop/recovery outcomes. Adapter queues still
retain GUI batching state; coordinator queue API is not sole GUI command storage.
Some presentation flags remain. Inventory: `docs/ipod-lifecycle-audit-2026-09.md`.

## Earlier fixes and protected evidence

Shared local acquisition tolerates exact-byte cloud timestamp drift, bounds
staging per track and avoids canonical reopening after capture. Missing owned
Rockbox media/covers now recreate safely. No-cache semantic stock no-ops preserve
original DB bytes. ArtFillWorker exact-instance retirement fixes the observed
stale-finished callback crash. Ctime-only source metadata drift no longer triggers
the badge; proofs/receipts still invalidate and reverify strictly.

Private git-ignored snapshot:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`.
4854 hash-verified files / 644906530 bytes, 24214 inventory rows, actual 1839-track
DB. Original device files are read-only. Audio/per-track covers/historical artwork
omitted; PIONEER excluded. Use only disposable test copies; sparse historical media
fixtures explicitly reject audio reads. Earlier `20260926-163557/` is INCOMPLETE.
Preserve generation-58 recovery including `20260926-152340-47477cfa4a49`;
old `copying` status proves neither commit nor rollback. Never restore a capture
or revive remembered writes without separately reviewed recovery authority.

The previously authorized fresh menu order was
`playlists,wps,plugins,settings,system_menu`; firmware may regenerate cfg.old/new.
Do not expand automatic config ownership to menu/theme preferences.
Recovery-count/DB-hash rows remain hidden; hardware identity memory requires exact
volume UUID/library ID and proven provenance. Unknown Rockbox serial/stock firmware
still needs a verified stock-mode observation; never guess from historic records.

## Installation and validation

Use the existing `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/` only.
Preserve pre-lifecycle.app and named previous build rollbacks. Older recovery at
`/private/tmp/soundbox-sync-performance-rollback.vlodpruy/` stays intact.
`/tmp/soundbox-lifecycle-install-state.py closed` snapshots library/cache baselines;
`verify` compares. Don't use its backup mode to create another directory casually.
Current closed baseline: 202 Favorites, 3298 artwork files, 2924 analysis files,
library SHA-256 `b51daa1e5a9307a12ff41da70b29fa11d7cffab5e2a6941ba26981f89ce11b91`.
`library-before-reconnect-fix.json` preserves pre-quit state in the rollback dir.
Full validation after final source changes:
`SOUNDBOX_IPOD_CAPTURE="$PWD/soundbox-ipod-ref/offline-lifecycle/20260926-163945" ./check.sh`
passed native build, pyflakes, mypy (36 engine files), 941 tests with four skips.
Runtime instruction parity and diff whitespace checks passed.
Clean release packaging log: `/tmp/soundbox-wrap-build.log`. The last verified
pre-release build log is `/tmp/soundbox-reconnect-install-build.log`.
The wrap-up installation retains `lifecycle-before-wrap.app` in the same rollback
directory; repeat the state verifier after relaunch. Install by graceful quit, verified process exit, rename+ditto,
signature verification, relaunch and curated-state comparison. Never wipe AppData
or use rm -rf to install. Rekordbox/source audio remain immutable.

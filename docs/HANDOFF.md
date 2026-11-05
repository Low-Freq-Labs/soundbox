# SoundBox current handoff

Updated November 4, 2026 for the USB, iPod and whole-app audit wrap-up.

## Current source and installed app

The completed audit and documentation changes are being published on `main`.
The release matches all 694 frozen source/test/build inputs and runtime fingerprint
`7ffde716cf03f74411a9256614e52ffd60a5c351fc56145a62b613d7be3f520d`.
Exact commit/push evidence is in the private wrap-up `publication.json`.

**Personal build 165 remains installed and running at `/Applications/Soundbox.app`.
Leave it running for Sonny's testing.** Its signed base remains
`19a3075339f3a0ad619f1faeacb5b79d6465c9cb`, dirty=true; publication does not
restamp, rebuild or reinstall that tested binary. Executable SHA-256:
`22fe4b7e3b2495a98ad3bc14def3766efb461823e3bcbcfe65a21da97a837fde`.
Installed 22:50:20, normally reopened 22:51:50 and verified 22:55:01
(America/Chicago; PID15767 at verification). Newer user activity supersedes the
state checkpoint below; this wrap-up uses no real-app inputs or termination.

## Completed changes and validation

- USB formatting owns its physical disk without blocking another USB's Export.
  Native erase stages are visible; selected USB Info artwork loads off-thread.
  Generic USBs remain explicit selected-playlist exports with no idle catalog/
  source comparisons or sync/pending badges, and no completion/eject verification.
- Stock/Rockbox/dual iPod planning shares bounded catalog/source observations and
  preserves partial-export ownership. Interrupted adoption freshly hashes affected
  files; coarse FAT epochs/edge hashes never authenticate their middle.
- Settings/device pages and export caches initialize on demand. Model/artwork/
  title cleanup, queue wakeups and state/history/Grid no-ops avoid repeated work.
  Idle decoding parks without altering active audio/render behavior. Import
  collisions and conversion-recovery races preserve newer files. Required atomic
  publication, fresh source/apps/authority/recovery and codec gates remain.

All **2,779 tests** pass: 2,713 offscreen in460.325s and66 Cocoa in68.917s,
zero skips or expected failures, complete worker/phase inventories. Test phases
sum529.242s, excluding the explicit display hold. Both signed profiles match
163 personal/159 public first-party Python modules, both entrypoints and
25 deck/5 process/17 CDJ native sections. Personal/public/installed isolated native,
process and RAM-only SQLCipher probes pass; populated/Settings/empty renders were
inspected. The corrected cue-style sequence passes187 ordered cases and ten
pixel-exact states. Failed gate/reproduction records remain retained; native tests
resumed only after Sonny unlocked and fresh console/process/source checks passed.

The source census covers184 first-party Python/native files:129 reported full
reads,29 targeted reviews and26 prior same-day exact-hash scoped reviews. Large
targeted bodies and dependency/firmware algorithms are not every-line proofs.
Details/counters/limits: [performance audit](performance-audit.md).

## Signed startup and actual reopen

Same3,740 generated snapshot-browser rows,20 warm launches each, all retained:

| Metric | Prior signed app | Current signed app |
| --- | ---: | ---: |
| Median |3151.935ms|1203.228ms|
| p95 |3178.523ms|1237.419ms|
| First row-cache miss |4185.058ms|1452.147ms|

Median improved61.826%. Scope includes process/imports/native initialization,
selection/search and synchronous Qt paint; excludes fresh-source verification,
compositor, persistent icon work, device readiness and machine-cold launch.
Normal reopen separately paints3298 snapshot rows, loads3296 native tracks and
85 playlists, schedules0 prep tasks and fetches0 art. Two startup device scans
(0.602s/0.147s), no recurring scans through22:54:56. Minute analysis discovery
is separate and produces0 tasks. The existing unavailable binding/unresolved
import and old32kHz MP3 limitation remain unguessed;0locators saved.

## Fresh state, evidence and retention

Fresh closed-app library SHA:
`5c32bb8741c9371e1250197461e675255ffb865d1dea848739fb037dbd5cfb0e`.
It, all10 authority JSON files, artwork/default indexes, signed Contents and white
Finder/Dock icon remain exact through the22:55:01 check. Counts:10 custom playlists,
100 imports,490 artist overrides,1109 titles and524 hidden; all five accepted
source/import/prep queues were empty. Only saved column state differed from the
prior iPod backup. Never restore an older checkpoint over newer user work.
Preserve124 converted originals, all native recovery, current curation, virtual
Artists and required iPod/CDJ/waveform fixtures.

Private implementation evidence:
`~/Library/Logs/Soundbox/soundbox-deep-audit-2026-11-04/`.
Prior physical FAT32/exFAT tests and iPod fixture proofs remain in the dated USB/
iPod audit roots and [performance audit](performance-audit.md). No future erase,
replay, restore, app control or expanded source authority follows from them.
Completed one-use installer/resume scripts are evidence, never replay instructions.

Wrap-up documentation/publication evidence:
`~/Library/Logs/Soundbox/soundbox-wrapup-2026-11-04/`.
Every current first-party document is fully reviewed; archives receive historical
scope/permission/link checks and pinned upstream references/licenses exact-byte
checks. Per-file hashes/depth/findings, dependencies and local links/anchors are
recorded there. No external-link, legal, firmware or new hardware acceptance is
claimed. Earlier handoff diary text is retained privately, not current instructions.

Five pending-acceptance bases share the existing rollback directory:
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-export-update-install-zvxfrqm0/`.
Immediate `Soundbox-before-whole-app-audit.app` is26f15fb4…/c6c3a247… . Earlier
`Soundbox-before-ipod-audit.app`, `Soundbox-before-usb-audit.app`,
`Soundbox-before-usb-simplification.app` and `Soundbox-previous.app` remain exact;
full hashes are in the deep-audit `protected-rollbacks.json`. Retain pending user
acceptance; no restoration authority is granted. Six redundant dist/COLLECT/
PyInstaller directories are already retired (1,116,266,340 logical bytes).
Keep installed app, source/venv, native/CDJ/check outputs, fixtures and recovery.

No implementation/install work remains. Sonny is testing the app. Physical iPod/
firmware/eject/reconnect acceptance, broader codecs/native analysis replacement
and distribution/dependency clearance remain separate gates. Do not restart the
app or mutate real library/device state for documentation or publication.

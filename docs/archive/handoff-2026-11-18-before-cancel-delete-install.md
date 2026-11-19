> Historical handoff checkpoint. Dated approvals grant no new app control,
> source/device writes, installation, replay, restoration or cleanup.
> Current guidance and installed identity: [HANDOFF](../HANDOFF.md) and [runtime contracts](../runtime-contracts.md).

# SoundBox current handoff

## Current runtime — stopped and iPod safely ejected

Installed personal172 now has source
`8e5da943c99d27ff76214008b943c855d0db218fb36651c277d4cd008ce74963`, executable
`d154e918ccc773c06d75b2145677c847ff828f1e138a6b4b6222f833a408ea04`, dirty HEAD
`d6b504e07a358483a0f56880e15a412c5e414803`. Installed07:49:03UTC, normal launch
07:49:53UTC. Signed code/icon/214authority-art files and strict whole curation
passed; all3298display pairs match. No new preparation or artwork fetches.
The private verifier initially refused its previous handoff path; its sole
directory-constant correction and original helper are retained. Source remains
separate from that diagnostic-only correction.

User then requested stopping SoundBox because stock playlist Delete, Cancel and
Quit were stuck. Root captured complete canonical manifest/referenced records
and11,162,596-byte portable state, verified metadata active/pending/ACKs zero,
and observed only a read-only Music WAV descriptor. With that explicit current
stop scope, SIGTERM stopped exact main17260 and helpers17275/17277; no SIGKILL.
Normal `diskutil eject /Volumes/SONNY` succeeded07:57:19UTC after fresh UUID/USB/
iPod checks; no force-unmount. App remains **closed**, device **ejected**.
Saved backlog is2076/3121, unpaused setting retained; it cannot advance closed.
Stop/eject proof: current gate `runtime-handoff/delete-stop-20261118/`.

Active source work is the next Cancel/fast-Delete correction; the installed8e5
does not contain it. Three whole audio reads per orphan explained the long
Delete plan/commit, with no bounded planning cancellation; background metadata
also stole its foreground caption. Unknown-to-Mac songs do not block native
Delete. Exact pending source intentions and all source/native DB Quit-drain
rules remain: user explicitly clarified **DB writes must finish before Quit**.
No source/drain relaxation is authorized. Current fixes/tests are in progress;
run a fresh complete gate/package/install before calling them delivered.

## Delivered correction — FAT32 temporary publication

Installed5a77 below failed physical Sync at its first recovery backup. The source
candidate `8e5da943c99d27ff76214008b943c855d0db218fb36651c277d4cd008ce74963`
changes only `soundbox/device_staging.py` among459 frozen first-party app/native
inputs. It compares the current retained descriptor with the no-follow stage,
keeping that descriptor open through cleanup instead of retaining an empty-file
inode that FAT changes at first allocation. Parent/mount/caller-byte fences stay.
Player, native load/cue paths and source writer protocols remain exact.

Seventeen focused staging tests/types/lint pass; the old code independently
fails the new allocation/cleanup controls. A real disposable FAT32 image using
the fresh copied current native catalogs/Play Counts passes211-song Sync,
readback and repeated no-op:207metadata rows/211covers, zero audio reads/copies.
Plan3.42s, commit2.65s, warm no-op0.96s. Both late-fault rollback controls pass,
including after database publication and Play Counts consumption; original
catalogs/raw counts and referenced pixels restore exactly. Unreferenced appended
pixel ranges may remain, as designed. Unknown extra device membership is kept.
No physical retry/recovery/cleanup was performed by the agents.

Ten permanent FAT32/exFAT image/lifecycle tests pass, with twelve real-filesystem
subcases rejecting the old code and independently reviewed image cleanup.
The complete bare gate passed4,176tests at07:44:28UTC:4,109offscreen in675.15s
and67Cocoa in79.62s, zero skips/expected failures,863unchanged frozen inputs.
The offscreen/native packaged renders, frozen helpers, native/crypto/signature
and exact packaged-code checks passed, followed by guarded install/verification
above. Do not reuse this gate as proof for the later changed Cancel/Delete code.
New evidence:
`~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/fat32-publication-gate-20261118/`;
copied FAT proof and physical post-failure capture:
`critical-ipod-current-sync-audit-20261118/publication-failure-20261118/`.

## Previous shared-art Sync correction — November18 (superseded by8e5 above)

`/Applications/Soundbox.app`: personal172, dirty HEAD
`d6b504e07a358483a0f56880e15a412c5e414803`, source
`5a773044ec919f355c5ec72c520c5c5724001a0df1ae032448b4142a988b5e34`, executable
`b9544db8c4fea76e1ca7cbad3e40d236e8a5476af5294e12fb72d088a917414a`.
Installed06:49:22UTC; normal background launch06:49:41UTC. Main11030 and owned
cache11035/native-read11038 helpers were verified; read fresh PIDs before use.
No commit/push. Tested source, packaged code and actual installed identity are
independently proved; documentation never restamps an older binary.

The complete bare `./check.sh --render` passes4,162tests:4,095offscreen and67Cocoa,
zero skips/expected failures and862unchanged frozen inputs. Lint/layout/native/
CDJ/mypy140/immutable fixtures pass. Offscreen and packaged Cocoa renders were
inspected. Signed first-party code/entry/hooks and25native TEXT sections match
source/builds. Generated full-size cache/native helpers and isolated native,
process, ORM/SQLCipher/AES checks pass without real state/device work.

The first full attempt ran4,095offscreen cases and failed one public-core fixture:
its relative fake catalog sat beside its USB target, correctly rejected by the
new complete source boundary. Only that fixture's catalog identity moved into
its existing generated source directory; every assertion/isolation rule remains.
All original failure/worker evidence is retained. Application source did not
change between attempts. The complete gate above then passed.

## Delivered behavior

- Native Music artwork can have several tracks referring to one MHII record.
  Both incremental saves and compaction preserve original image IDs, physical
  header owners, native order/opaque metadata and untouched pixels. A selected
  changed cover splits away from retained aliases. Captured alias witnesses
  permit valid native sharing while missing/forged links still refuse writes.
- Unchanged native MHIT count/size fields remain exact, including the current
  device's size one byte below physical MHII source size. Fast-sync shadows bind
  actual referring tracks and their current links, independently of header owner.
- Stock and Rockbox cover preparation hash/encode one stable captured buffer.
  Warm source reuse binds device/inode/size/mtime/ctime and retains the no-read
  fast path. Legacy incomplete source indexes miss safely. Rockbox v7 retires
  potentially poisoned v6 associations; malformed derivative hashes repair safely.
- Mounted Rockbox operations keep original device identity through reads,
  nested Sync, publication, descriptor-relative pruning and finalization. Deferred
  engine/CLI/GUI cleanup compares the original mount identity before traversal.
  Raw inputs include catalog, hidden media, analysis and native aliases in their
  source boundary. An empty GUI written-dir set skips recursive cleanup.
- Device/song/playlist types use **iTunes**, **Rockbox**, or **iTunes/Rockbox**,
  with readable column widths. Generic USB retains explicit selected **Export**;
  **Sync belongs only to iPods**. No automatic USB sync status was introduced.

Video and Classic keep their distinct observed formats, signing/art profiles and
fresh per-device capability guards. No native source writer/profile expansion,
new source pilot, audio rewrite or actual physical Sync commit was performed.
The accepted fast player/cue implementation remains: PlayerBar and the three
load/cue/publication callers match accepted19d code exactly;252protected source/
native files remain unchanged. Full native waveform/playback checks pass.

## Actual copied iPod proof and physical acceptance

Sonny's actual connected iPod was captured read-only with mount/UUID guards:
iTunesDB, ArtworkDB, Play Counts and all three complete ithmb files. Full actual
capture/materialize/recognize/plan/TEMP-commit/reopen/no-op runs use current frozen
Favorites display and17.16MB of selected actual artwork. Installed19d reproduces
the exact shared107 failure; the previous shadow loop independently fails its
new regression. Corrected planning3.30s, commit1.19s, repeated no-op0.80s.

The211source songs match211of212device entries:207metadata records/621fields and
211covers update, with zero source/retained-audio opens or copies. All original
ithmb prefixes, playback history, technical fields and playlist memberships
remain exact. Fresh comparison has clean matched metadata/art and an honest
remaining warning for the unmatched extra; no guessed deletion or ownership.
A real QtCore worker/TaskOwner run has maximum planning heartbeat gap16.39ms on a
10ms timer. This is not hardware playback, compositor or audible-latency proof.
Six generated pipeline controls additionally cover alias splitting, unselected
native preservation, unknown entries, genuine new-audio addition and warm shadow
reuse without planner/artwork/commit mocks.

Physical Sync acceptance **failed** on November18: the installed app reports
`Device temporary file changed before publication`. The earlier complete copied
pipeline used APFS TEMP targets and did not prove FAT32 publication semantics.
A fresh read-only post-failure capture proves iTunesDB, ArtworkDB and all three
ithmb payloads remain byte-identical to the earlier capture. Play Counts changed
after user playback and must be preserved. Recovery inspection and a disposable
FAT32 reproduction are in progress; no device replay or recovery is authorized.
Keep the fallback and all required transaction evidence. Private evidence is in
`critical-ipod-current-sync-audit-20261118/publication-failure-20261118/`.
Current capture has no zero-explicit-link art-bearing rows.
Sonny had already accepted rapid consecutive loading and creating/deleting cues
while playing on19d; this correction preserves those paths.

## Curated runtime and resumed work

An exact source-scoped pause drained at1,842/3,121 with zero field/receipt work.
Complete pre-Quit/post-Quit manifests, referenced records and11,060,212-byte
portable snapshots are preserved. Live pre-Quit capture is read-only/bracketed;
only its private copy constructs a StateArea. A generated proof confirms no live
area/recovery/write. Normal Quit initially returned AppleEvent-128, then exited
naturally; no repeated/forced termination. All non-preference curation matches.
Replacement preserves signed Contents, white custom icon/Finder resources and
214authority/art JSON hashes, reusing the sole accepted f502 fallback.

Paused runtime verification passes every curated field and all3,298actual engine/
TrackInfo/MetadataController title/artist pairs. One already-completed title
mask/shield (local242088333, position1,816) retires only with independently proved
native ID/UUID/path/own-ACK/target equality; no pin retires. All other logical
state remains exact, including100imports,491artist overrides,10custom playlists
and16manual analyses. Existing missing-file/binding diagnostics remain untouched.

Startup has zero analysis/compact/grid/cue preparation and zero artwork fetches.
The first private verifier wrongly required only one zero-task pass: the existing
60-second single-shot maintenance timer produced further empty passes. Its strict
failure/script and timer/log proof are retained. The corrected private oracle
still refuses every nonzero task and preserves all other checks; no production
or test change was made for this verification.

The exact Resume action restored the accepted backlog. At06:58:25UTC it is
unpaused and active at1,853/3,121, with exact frozen scope/order/targets and no
queue error. Preserve later progress; never restore this historical cursor.
Native metadata authority remains
`e707861f3db79f04a783b5fd44d80b11ab893961f3990dd37447d59fe0635655`, profile
`unicode-album-cloud-metadata-v5`, library629922790, rekordbox6.8.7.0303.
Accepted source writer SQL/profile hashes are unchanged.

Sonny observed a Resume label while saves were running. Actual state was
unpaused and advancing. The automation's AXPress can leave its pre-action popup
visible; its first conditional dismissal was insufficient. A manual close/reopen
menu check is pending. Do not toggle a running backlog to investigate a caption.
The production menu issue was deferred until testing, then Sonny reopened it.

## Further audits and retention

Parallel audits produced separate **unapplied** future findings/prototypes:
USB editor drafts can cross device owners or be erased by refresh/ACK; deferred
catalog Sync can strand a batch on cancellation/source change; legacy unique
song-ID artwork fallback can lose covers; absent-folder raw export has a final
mkdir admission race. The mounted-root correction above does not claim exhaustive
new-folder admission safety. Current iPod artwork uses explicit links, so the
legacy fallback case is not in this capture. Private prototypes/limits, earlier
player intent/keyboard/delta work and remaining review are in
[the audit ledger](../critical-audit-2026-11.md). Do not mix them into the frozen app.

Private gate root:
`~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/stock-artwork-shared-sync-gate-20261118/`.
It holds full gate/package/helper/render proofs, the failed fixture attempt,
current scope and `runtime-handoff/` complete checkpoints/install/runtime/resume
proofs. Actual copied-iPod inputs/proofs are in
`~/Library/Logs/Soundbox/critical-ipod-current-sync-audit-20261118/`.
Other generated audits are in `critical-browsing-ux-audit-20261117/`.

Keep the sole accepted previous f502 app at
`stock-sync-cue-gate-20261117/runtime-handoff/previous/Soundbox.app` until physical
acceptance, plus current required captures/recovery/fixtures. Never create a
second app rollback. f502 lacks newer optional cue bindings: retaining it does
not authorize replay/automatic rollback over pending jobs or newer curation.
Retire the previous app and duplicate packaging only after accepted verification.
No source/device restore or diagnostic cleanup was performed in this correction.

Rules: [runtime contracts](../runtime-contracts.md). Playback: [PLAYER](../PLAYER.md).
Devices: [DEVICES](../DEVICES.md). Earlier state:
[dated prior handoff](../archive/handoff-2026-11-18-before-shared-art-sync-install.md).

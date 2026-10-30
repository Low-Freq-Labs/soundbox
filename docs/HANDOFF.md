# SoundBox current handoff

October30, 2026. Branch **main**; runtime release5bf8141 and provenance4eef8a9
are published. **Build148 remains installed; this follow-up is a plan, not a
runtime fix.** Sonny will restart context. Begin with the
[shared-workflow implementation plan](shared-library-workflow-plan.md), then the
code/evidence paths it lists. No subagents are authorized. Sonny operates
rekordbox UI; never drive it remotely.

## Next session: active scope and first actions

Sonny reports slow visual playlist deletion, misaligned sidebar glyphs, RGB-only
wiggle/pulse/glisten at five/six zoom-in steps, blocked direct imports into native
playlists/Collection, and blocked manual BPM correction on SoundBox imports.
The required outcome is **the same native rekordbox DB/sidecars as SoundBox's
shared primary library**, independent of import/analysis author. Ordinary edits
just save compatible native records; no separate Save to rekordbox step or
routine app-branded save/footer messages. AppData holds UI/cache/identity/device
state, pending drafts and recovery, not a competing musical-data authority. The plan covers per-component reconciliation, native
registration/analysis publication, Collection-only discovery and real end-to-end
acceptance; do not stop at UI polish or register-only imports.

Sonny clarified the initial missing ABBA BPM/key was because automatic analysis
was off. He has now enabled automatic BPM/Grid+Key. Keep that setting. The current
ABBA result is119.85444071310947BPM /7A, full131.536-second analysis. Manual BPM is
blocked because the existing controller accepts only analyzed native tracks.
Sonny explicitly wants steady near-integer detections to prefer whole BPM
(119.85->120,139.90/140.10->140), with the actual grid corrected, not display-only
rounding. Implement local manual correction independently of registration, and
validate a whole-tempo prior without overwriting deliberate fractional/variable
or existing manual/native grids. RGB targets are128/184px/s if counting clicks;
include92px/s to cover the alternate counting convention. BLUE is solid.

First: read the plan/code map, preserve fresh current state, reproduce the local
manual-edit and near-integer cases, design shared identity/component transitions,
then implement/prove in the stated sequence. Profile deletion feedback and use a
durable presentation overlay; it must not pretend an uncommitted source save.
Reuse the captured native import/key/grid/cue examples before requesting any new
Sonny UI steps. New registration/analysis/metadata authorities still need their
own copy-only proof and native acceptance. Existing scopes are not broadened.

Private current-case evidence/screenshots:
~/Library/Logs/Soundbox/shared-workflow-plan-2026-10-30/.
observed-case.json and library-at-plan.json at2026-10-30T16:52:41.455423-05:00 bind a newer state with
**85 imports /1104 title overrides**, library SHA 44f1427958abe331358102916457a1b0769a0fc7d38ebf2cf0230c0ee55512bc.
ABBA local ID sb-49963b9f4749; exact canonical-path query on a private source
snapshot found no active native row. Fallback cache has260 zero-anchored rows at
118.42; later RBXport result is separate. Do not mislabel the fallback as completed
BPM/key analysis. Re-read current state at resume: Sonny is actively testing.
**Never restore the older84/1103 release baseline below.** No app/source/audio
mutations or analysis runs were performed by the agent in this planning pass.

## Installed provenance and validation

Build148 was built from base1808a74409fb72fbae6be4e28b557a984f1c290c with dirty=true;
its signed runtime fingerprint is
`ddeccc7235d0d68c90947ebd390fec748de2b1407f992748b08bce8b749cad8a`.
This exact runtime source is in the release; later documentation does not restamp
or require rebuilding the binary. Composition, signature, complete embedded Metal,
CDJ schema and native/CDJ text match the checked source/binaries.

Bare `./check.sh --render` passed1700 tests:1640 offscreen415.482s and60 native
Cocoa64.357s, zero skips/expected failures; Rust, mypy74 and fixture gates passed.
Native folder/menu renders and the installed screen were inspected. Isolated
packaged smoke passed with native=true and no library/device/account discovery.
Actual launch and normal restart loaded3742tracks/76playlists/Collection3230,
reused3106 artwork entries and scheduled zero waveform/analysis preparations.

The earlier locked-screen presentation failure and Keychain-held shutdown are
retained as historical evidence. Sonny unlocked the display and dismissed the
SoundCloud Keychain request. The original process exited normally; continuation
captured its final save and relaunched148. Fresh restart-log offsets verify the
second startup. No process/worker was terminated to pass the restart check.

Evidence: `~/Library/Logs/Soundbox/playlist-management-2026-10-30/`.
Key records: check-unlocked.log, passed-{offscreen,native}.json, installation.json,
state-verification-post-restart.json, final-state.json, final-analysis-verification.log,
column-state-serialization-proof.json and final-environment.json.

## Historical build148 verification state — not the current library

Post-restart baseline at2026-10-30T14:32:01.087654-05:00:
library SHA `88a9b5f349d0eff6cd6d2524279995c346ea9b8aa9cb56323a64529894428753`;
curation SHA `de29a5330cb48ecfeed5e8e3aefd4b7fbd68509966d7e8faf1c60dab525d4d68`.
Custom10/import84/artists489/titles1103/hidden517/removal groups1/additions0/
aliases24/settings29. All4041 artwork/review files,3282 waveform hashes and4 source
authorities remain exact. Title117829415 is Zeds Dead - Rudeboy (Ruudy Flip).
At that release checkpoint, normal quit changed only Qt columns_state serialization: independently restoring
both headers proves identical order, widths, hidden sections and sort. Relaunch
made no saved-state changes. Native playlist FIFO is empty; both reference UUID
pins remain. Source DB/XML are unchanged by install/restart/cleanup; no journal.

## Completed behavior and remaining work

Ordinary native playlist/folder create, rename, delete and registered-track append/
remove passed11 exact native DB/XML/rollback replays,12 guarded source operations,
Sonny's native inspection and normal save. Production authority is provisioned.
Durable FIFO/retry/discard shares Tag/Cue/Grid ownership, lock and journal; native
apps must close for saves. Catalog/cache9 preserves empty folders and clocks;
exact UUID pins retain affected Collection songs. CDJ capture waits for source
intents and rebinds stable native IDs. Full scope: analysis-playlists-next.md.

At release acceptance, Bridge Pilot3604899226 contained only102012532 after the
requested native Key-only removal. Sonny has since deleted that playlist: a
planning-time private snapshot found no row. Do not recreate it. Both reference
tracks were retained in Collection at release; re-read current source at resume.
Own acceptance folder2463365283/child2933229850 and synthetic NEW-row tests are
gone; never recreate them or reuse their approvals. Only the corresponding
October30 local veto was migrated; unrelated July vetoes remain.
Source629922790 / DBVersion6000 / rekordbox6.8.7.0303, schema
`c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08`.
Shared AppData recovery: rekordbox_recovery/tag-writes/b496f6c142799c5315d33799.
All12 full encrypted acceptance pairs, approvals and native captures are protected;
ordinary retention keeps the latest full pair plus permanent row/XML receipts.

Chris Le audit inventories117 repositories and pins five relevant trees. Existing
RBXport vendor pin remains unchanged. Adaptive/attack analysis is now the default,
with bounded30-minute/96M head, streamed tail validation and truthful legacy/
partial-result provenance. Native/manual grids and cues win. The 3BAND mini
retains all seven overlap colors. All39 checked main frames across3modes/13zooms
are byte-identical before/final-after. See rbxport-reference-audit-2026-10.md and
private `~/Library/Logs/Soundbox/rbxport-audit-2026-10-30/`.

New-file registration, reverse detected-analysis publication, intelligent/cloud
playlist mutations, physical iPod acceptance, wider CDJ/model/firmware acceptance
and distribution remain separate work. No USB publication occurred this session.

## Retention and space

Explicit cleanup reclaimed~8.04GiB in its measured pass; final available space is
~29.82GiB.95 independent APFS clone consolidations retain every byte/path of164
encrypted captures, saving~5.57GiB. All16 archive receipts/payloads verified.
Runtime baseline.db and active journals were excluded.47 large diagnostic JSONs
are transparently compressed. Runtime log keeps its latest2MiB; complete previous
history is hash-verified gzip. Evidence: `~/Library/Logs/Soundbox/garbage-collection-2026-10-30/`.

Installed148 plus **only fallback147** remain. Rollback:
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-analysis-rollback-flpz99ko/Soundbox-build147.app`.
Old138/140/141/142/146/info-layout apps and regenerated packaging/compiler/mypy
output were retired under the explicit cleanup instruction. build is~16MiB:
native dylib, checked CDJ release binary, reports and copy-only proof. Dist is
absent. Retain active venv, all frozen/native/iPod/CDJ references, generation58
recovery, source approvals and encrypted archives. Shared extents make apparent
folder sums misleading; do not delete recovery based on du alone.20GiB waveform
reserve remains unchanged. Keep147 until acceptance or explicit later cleanup.

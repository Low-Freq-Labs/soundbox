# SoundBox current handoff

Updated November4,2026 after installation at03:50:00, normal reopen at03:50:56
and verification at03:53:21 (America/Chicago).

**Personal build163 is installed and running at `/Applications/Soundbox.app`.**
Sonny closed build160 himself, then explicitly requested installation/reopen.
No real-app inputs or app-process termination were used. Leave the new session
running; no documentation-only rebuild, restamp, reinstall or restart is needed.

## Runtime and source provenance

Branch `main`. Sonny's subsequent November4 wrap-up authorizes publication of the
tested USB/UI source. The installed signed base remains
`740ad533c4991938f504d792a89020623f063471`, dirty=true; publication does not restamp,
reinstall or restart it. The exact tested runtime is:

- Runtime/source fingerprint: `42aefd3544c3ee60af5635110d3f99a5385b14fcb0f4dde5b3541ed6434e745c`.
- Executable SHA-256: `e8d27b4a6eaa97168da25cc6525b1f81c33f4ff35538fb692f733ae5acfb321a`.
- Frozen source/test/build inputs:690, all exact through packaging/installation.
- Packaged Python parity:161personal/157public first-party modules plus both
  entrypoints. All25deck/5process-guard/17CDJ file-backed native sections match
  the tested builds; signing metadata is separate from executable section bytes.

Both package profiles passed strict signing and isolated native startup before
installation. Installed isolated startup passed too, with native CoreAudio/Metal,
process guard/query and RAM-only ORM/SQLCipher checks, zero library/device/artwork
work. Saved white Finder/Dock metadata remains exact. Deep signing and exact
signed Contents pass after normal startup. Never strip the saved icon or re-sign
a running app to hide strict root-metadata rejection.

The prior installed build160 was runtime7bbe8577…/executable380806ce…; its signed
base was c2415d5…/dirty=true and its published tested source was4a3d29a…. Those
are historical identities, not the current runtime. Source publication later
must not restamp/reinstall these already verified bytes merely to change HEAD.

## Delivered USB/UI behavior

Generic USBs are explicit selected-playlist export destinations. They never show
Sync needed, Sync status pending, pending changes or automatic database-drift
badges. Connection, idle polling and source-only edits do not read their catalogs
or compare their contents with the desktop library. Mount notifications/kernel
hints still detect connections; physical/write identity remains freshly guarded.

Only selected playlists are exported or updated. Other playlists, unrelated
collection records and media remain intact, including unrelated differences
between Classic and Plus. Retained audio is not audited. Replacing a selected
playlist prunes removed songs/assets only when no remaining playlist in either
native catalog uses them; shared assets survive. Recovery and manifest-last
publication remain, with removed-media rollback retired only after durable commit.

Catalogs load on a deliberate USB/tab click. Already-checked tabs can refresh;
internal page restoration and row expansion do not start reads. Duplicate in-flight
requests coalesce. Successful writes clear stale song rows with an honest browse
notice while preserving settings drafts/view state. Failed, cancelled and unchanged
operations retain their view. Completion starts no final USB read. Eject cancels
an idle discovery pass at its safe boundary, waits for actual worker retirement,
then requests only normal macOS eject. Successful eject removes its row/caches in
memory, without an extra scan. No forced unmount or new verification is added.

Export opens with folders collapsed. One through five devices fill the right
panel with large names and USB/iPod icons. Checkboxes follow the current/live
accent. Progress uses the saved accent-text color over its fill and normal light
text over the empty region. USB/iPod playlist-browser folders and playlists toggle
without selection highlighting; only tracks select. Keyboard navigation, stock
reorder and checked deletion remain. The artwork-review spinner, hex editor and
duplicate-dialog text selection also respect the theme. Fixed transport/cue/
waveform/native color meanings remain independent.

Source export capture queries selected cue/playlist memberships in bounded chunks,
skips unrelated smart-list evaluation, avoids unnecessary Collection/Artists view
construction and removes a duplicate initial USB catalog read. Small catalog,
source, transaction and affected-path checks needed for valid native publication
remain. Stock/Rockbox iPod sync semantics, firmware capabilities and all desktop
native authorities remain independent and unchanged.

## Physical USB validation and format behavior

Sonny explicitly authorized reformatting/export tests on TEST-1 and TEST-2,
superseding the earlier documentation-only instruction to leave them untouched.
Both were formatted and initialized successfully, with fresh physical/source
separation and postcondition guards. Same-filesystem formatting can quick-erase a
correctly typed, full-capacity first/sole MBR partition; other layouts or filesystem
changes repartition. Native initialization now removes the offline renderer's
advisory lock before enumerating player assets. The first hardware attempt exposed
that lock leak; the fix and a real initialization regression passed before the
successful results below.

| Dated target | Format | Format incl. guards | Prepare / publish empty catalogs |
| --- | --- | ---: | ---: |
| TEST-1,31,042,043,904bytes | FAT32/MBR, quick erase | 13.118s | 1.401s / 2.141s |
| TEST-2,256,060,514,304bytes | exFAT/MBR, whole-disk erase | 2.889s | 1.455s / 0.098s |

The macOS erase commands themselves took11.840s and1.812s. TEST-2's former layout
missed the conservative capacity-reuse bound. Observed volume UUIDs were
5B23EC65-A2C0-3E98-A4CA-282B0D9C4895 and6031AACD-4EDE-30E2-816B-9A8191DF24FA,
on disk5s1/disk4s1 respectively. Disk numbers/UUIDs here are dated observations,
never authority for a later erase; always capture the actual target afresh.

Each drive passed a frozen-fixture sequence through actual acquisition, renderer
and transaction: export two playlists, update only the first, preserve the second,
then repeat with no changes/writes. During update/repeat, guards rejected opening
unselected source audio or retained USB audio. Final selected memberships/payload
SHA matched and no pending journal remained. Unchanged repeats planned in0.270/
0.247s and committed in0.003/0.001s. The drives currently contain fixture playlists.
These small tests do not prove player/firmware, full-library performance or iPod
physical acceptance. No matched old-code hardware speedup is claimed.

## Validation and review scope

The final bare `./check.sh` passed all2595tests:2531offscreen in579.369s and64native
Cocoa in67.506s, zero skips/expected failures. Lint, mypy106core files, native/Rust
checks and required offline fixtures pass; all243pinned vendor files remain exact.
A prior full gate found an iPod tab-navigation regression plus an outdated USB
browse expectation. Passive tab navigation was restored alongside explicit-click
refresh, expectations were corrected,118focused tests passed, then the complete
gate passed. Keep the failed reports and correction scope as evidence; do not
weaken thresholds or label that failed run successful.

The theme-focused scan covered all60desktop Python files, with deeper inspection
of16style/color modules. White/black progress, current/live accents, one/two/five
choices and main/USB views were rendered and inspected; focused Cocoa gestures
passed. This is not a new exhaustive dependency/algorithm audit. Existing broader
review and measured remaining costs are in [performance-audit.md](performance-audit.md)
and [ui-polish-audit.md](ui-polish-audit.md).

Current docs distinguish generic USB policy from retained iPod sync behavior.
Earlier75-document reviews, historical acceptance and the58pinned-upstream missing
link targets outside the vendored subset remain dated scope, not new acceptance.
No exhaustive remote-link, newer player/OneLibrary hardware, iPod reconnect/power-
cycle, public clean-machine/notarization or broader codec/profile proof is claimed.

## Curated state and normal startup

Fresh post-user-quit library bytes remained exact through installation, installed
isolated startup, normal launch and03:53:21 verification:
`e1ae0d5bf2d8e62f7be554a0bd504ee38df571d559e1d5b39c8f1b7fa6ed395f`.
All10independent authority files, saved playlist choices and white icon remain exact.
Dated counts:100imports,10custom playlists,524hidden,1109title overrides,490artist
overrides,16saved analyses and98native bindings. All five import/analysis/prep/
playlist queues were empty. Never restore an earlier checkpoint over newer edits.

Normal launch loads3296native tracks/85playlist nodes, reuses3291unchanged analyses,
and schedules0prep tasks. Art fill fetches0. Both USBs were detected with one
0.36s startup scan; no recurring scans appeared through03:53:21. No stock-art
preparation starts. This is an observed startup window, not a general benchmark.
The known missing native binding/unresolved imported path and old32kHz MP3 error
remain explicit and unguessed. Preserve515user disposals/four local dismissals,
124converted WAV originals, all native recovery/acceptance and newer user edits.
Artists remains virtual-only; never recreate its removed1527native entries.
Prior Mocha145/9A/425-beat proof and separate WAV/CBR-MP3 authorities remain accepted;
this USB/UI work adds no desktop source publisher or codec/profile acceptance.

## Evidence, rollback and remaining work

Private evidence: `~/Library/Logs/Soundbox/usb-simplification-2026-11-04/` contains
source freezes, final reports, failed tab-gate reports, reviewed renders, physical
format/export receipts, package/entrypoint/native parity, all isolated profiles,
fresh state/authority/icon backup, completed installation receipt and normal
verification. Never replay `install_validated_update.py` or any prior installer.
Earlier release evidence remains under export-polish-2026-11-04 and the prior
UI/CDJ/theme documentation roots; archived checkpoints grant no restore authority.

The immediate rollback is
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-export-update-install-zvxfrqm0/Soundbox-before-usb-simplification.app`
(runtime7bbe8577…, executable380806ce…). The previously protected
`Soundbox-previous.app` in that same private directory remains
(runtimeb0ccb1ab…, executable410196e9…) pending acceptance. Both are existing
pending-acceptance bases; retire them under the standing cleanup rule once Sonny
accepts the replacement. Do not recreate the already retired fc696312…/d5cb4451…
apps or accumulate further rollback directories.

After verified installation, duplicate personal/public dist/COLLECT/PyInstaller
outputs were retired:1,115,703,444logical regular-file bytes, exact paths in
packaging-retirement.json. Installed app, active source/venv, build/native,
build/cdj, build/check, originals and required fixtures/recovery remain protected.
No requested implementation/install work remains. The November4 wrap-up
authorizes committing/pushing the tested source and documentation. Leave build163
and Sonny's session alone while he tests; no further device writes or acceptance
actions belong to publication.

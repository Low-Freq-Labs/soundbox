# SoundBox current handoff

Updated November4,2026 after installation at01:59:52, normal reopen at02:01:17
and curated-state verification at02:02:40 (America/Chicago).

**The updated personal build160 is installed and running at
`/Applications/Soundbox.app`.** SoundBox was already closed. No real-app inputs or
process termination were used. Leave the reopened session alone; do not quit,
restamp or reinstall it for documentation. Sonny’s subsequent November4
“wrap up” authorizes committing/pushing the tested source while he uses the app.

## Runtime and source provenance

Branch `main`. The installed signed base is
`c2415d5c3e9874919ca07b54b04636a282203ee2`, dirty=true. The November4 wrap-up
authorizes publication of the tested UI/export source, following the earlier
`d08ee91d18fe89e9fa520f7f242d1cd3015ec903` release and documentation closure.
The tested source release is `4a3d29aab0eb97a34bcce445cb6d70df3e1c1685`; its following
documentation closure records this handoff. Source publication does not change
the running bundle’s declared base or bytes.
Both old and new artifacts use Git-count build160; distinguish them exactly:

- Current runtime: `7bbe8577a80ba08fdef36efb4de1b3877820a2974a4b534ce36d6c43884fbfc8`.
- Current executable SHA-256: `380806ceb13f4ca40432231e8598a788193e894c2a6991a63a65136515138f49`.
- Previous November3 runtime: `b0ccb1aba55b0c005938b97d3e8a8e147670658bca9702ba2c1b04b49226af3a`.
- Previous executable: `410196e938f75df8d3cf6b5ed0176282b55513d6dce195437558f97592b8019d`.

All160personal/156public first-party packaged modules, both entrypoints and
26deck/5process-guard/20CDJ native sections match the tested source/builds.
Both candidates passed strict signing before first launch and isolated native
CoreAudio/Metal/process/RAM-only ORM/SQLCipher checks, with zero device, library
or artwork work. Installed isolated startup passes too. Saved white Finder/Dock
metadata remains exact; deep signing and exact signed Contents checks pass.
Never strip that metadata or re-sign a running app to hide strict root-metadata
rejection. The675-input source/test/build freeze is exact.

## Delivered behavior

Info prepares the selected cover off-thread before modal presentation, so its
first visible frame contains the settled cover or missing-art state. Timers and
worker results continue during preparation; owned work drains before closing.
New artwork publication refreshes matching dialogs without a late obsolete image.
The wait wheel now replaces only native white pixels with the selected accent;
black, transparent pixels, shape and hotspot remain exact. Five-accent pixel
proofs and native Cocoa first-frame/lifetime checks pass.

The device header now says Export. Its dialog has playlist/folder checkboxes on
the left and connected-device checkboxes on the right. A sole target is selected
automatically; multiple targets can be checked. One action queues targets through
the existing serialized device lane. Source binding and stable playlist IDs are
rechecked/rebound for each target; mount/UUID checks prevent redirection to a
remounted device. Target errors aggregate and later targets can continue;
cancellation/shutdown clear pending work. Existing stock/dual planners and their
first-generation review/capability boundaries remain intact. iPod physical
acceptance is still separate, as Sonny requested testing it later.

Selected USB exports no longer require unrelated classic/Plus catalogs to be
identical. The reported error was reproduced from three small read-only copies
of RB USB TEST: Plus had112songs, Classic111, while all four playlists agreed.
A final local merge succeeds while preserving that unrelated difference. Each
format's unrelated records/memberships remain its own baseline. New IDs/artwork
reserve both namespaces; different recordings sharing a numeric ID are protected.
Selected track playback records and exact playlist memberships validate in both
formats. Plus-only display remains supported without requiring Classic/EXT files.

Sonny's explicit space policy supersedes blanket USB collection retention:
replacing a selected playlist removes old memberships, then removes the removed
song only when no remaining USB playlist uses it. References in both native
formats protect shared songs/assets. Unrelated collection-only songs survive;
there is no whole-drive orphan sweep. Affected removed assets get exact-byte
checks and same-volume rollback. Pre-commit failure restores originals; durable
committed cleanup retires those media backups so the bytes actually become free.
Interrupted retirement resumes. Metadata recovery remains. New copies still need
peak transaction free space before commit. No new desktop source publisher,
codec/profile authority or source-audio mutation was added.

Earlier text/DPR/footer/menu and selection/scroll fixes remain included. The
[polish audit](ui-polish-audit.md) distinguishes the dated179-file review from
the new Export module and deeper selected-export/format checks. Review depths
and remaining GUI/state/network/geometry costs are explicit; this is not a new
exhaustive dependency, algorithm or physical-hardware audit.

## Format review and devices

Sonny explicitly requested a subagent review and permitted wiping the two then-
connected USBs. Read-only review found separate physical external writable disks:

- RB USB TEST: disk4/disk4s1,256060514304bytes,
  UUID5B29E617-ACFB-32C2-B580-077A6BF02C64, FAT32/MBR,32KiB clusters.
- TEST-USB: disk5/disk5s1,31042043904bytes,
  UUID25EB2954-2428-368D-B4C1-AA7D8C14614E, FAT32/MBR,16KiB clusters.

Both already have one data partition with1MiB alignment. No erase was needed;
no USB export/erase/media audit was performed by the agent. Disk numbers are
dated observations, never persistent erase authority. FAT32 retains original
CDJ-2000 compatibility. SoundBox also supports exFAT; official manuals/support
confirm it on CDJ-3000 and CDJ-3000X. Sources and exact review findings are retained
in `drive-format-review.json` in the evidence directory below.

The review fixed format source separation to include native analysis locations,
plus post-erase checks for stable physical identity, MBR, exactly one first data
partition, filesystem, volume identity and mount before initialization. Changing
GPT→MBR may legitimately change DiskUUID; stable physical fields still bind the
operation. Mock-only tests cover both FAT32/exFAT and rejected target/layout
changes. No formatting command was issued to real hardware.

## Validation

Completed release reports reconcile all2564tests:2500offscreen in574.225s and
64native Cocoa in67.450s, with zero skips/expected failures. Lint, mypy106core
files, pinned243-file vendor verification, Rust checks, required fixtures and
inspected main/Export renders pass. An extra39targeted Cocoa cases pass.

The bare full check's first native phase had one cadence failure while the Mac
was at4%battery:65presentations against a108minimum for a120Hz target. After
Sonny connected AC, the focused cadence check and the complete64-case native
phase passed without a source change. `verify_test_reports.py` then accepted
both complete inventories. Keep the failed run as dated environment evidence;
do not weaken thresholds or claim it passed on battery. Earlier gates are also
explicitly superseded: an old selected-name policy expectation, then two added
edge cases changed the inventory during an otherwise passing run. None is final
release acceptance. All675frozen inputs match both packages and the installed app.

Physical multi-USB write acceptance, iPod reconnect/power-cycle, broader player
controls/newer OneLibrary hardware, public clean-machine/notarization and native
analysis profiles outside their accepted independent authorities remain separate.

## Curated state and normal startup

Complete library bytes are exact across fresh preinstall capture, installation,
installed isolated startup and normal relaunch:
`227896a9c7cb7af133b5260723f7e63649acc460e7fbf97222552248e64ed9b1`.
All10authorities, saved playlist choices and white app icon remain exact. Dated
counts:100imports,10custom playlists,524hidden,1109title overrides,490artist
overrides,16saved analyses and98native bindings. All five import/analysis/prep/
playlist queues are empty; no JSON paths changed at02:02:40. Later user edits
always supersede this capture; never restore an older snapshot over them.

Normal startup activates the installed CoreAudio/Metal deck, loads3296native
tracks/85playlist nodes, reuses3280unchanged analysis records and schedules0prep
tasks. Art fill fetches0. Both USBs are detected; steady scans take roughly
0.11–0.17s in this dated startup, not a universal benchmark. Stock-art preparation
does not start. The known one missing native binding/unresolved imported path and
old32kHz MP3 conversion error remain explicit and unguessed. Preserve515user
disposals/four local dismissals,124converted WAV originals, every newer saved
edit and all native recovery/acceptance material. Artists is virtual-only; never
recreate its removed1527native entries. Prior Mocha145/9A/425-beat source proof
and separate WAV/CBR-MP3 authorities remain accepted; no authority was broadened.

## Documentation consistency follow-up

Sonny asked to check all guides/roadmaps for stale information after wrap-up.
The November4 pass covers all75tracked Markdown documents:39current first-party,
11archived and25pinned upstream. It reuses the prior38full-content reviews with
subsequent changes, new audit content and complete-corpus status/behavior/link
checks. Stale Export/collection-retention/global-catalog-equality wording, old
build157/source pointers and ambiguous historical validation claims are corrected.
The roadmap retains genuinely open physical/codec/distribution gates.

All648local links/18anchors were checked with no first-party/archive errors.
58upstream targets are absent from the pinned vendor subset; those documents
remain exact, and no exhaustive external-URL check is claimed. All243pinned crate
files are verified unchanged. Per-file before/after hashes, review basis and
findings are under `~/Library/Logs/Soundbox/documentation-consistency-2026-11-04/`;
PROJECT_LAYOUT records scope. This is documentation-only: the675runtime/test/build
inputs and installed executable remain exact, with no rebuild/reinstall,
native-library/device writes, real-app inputs or playback interruption. Preserve every newer
user edit and the current rollback while Sonny tests the update.

## Evidence and retention

`~/Library/Logs/Soundbox/export-polish-2026-11-04/` retains the675-input freeze,
final reports/renders, native battery failure plus AC rerun, format subagent
findings, focused regressions, package/native parity, isolated checks, fresh
curation/authority/icon capture, guarded installer and installed/normal proofs.
Never replay `install_export_update_once.py` or earlier installers/pilots.
Previous UI evidence remains under `ui-polish-2026-11-03/`; catalog reproduction
and cursor pixel proofs remain under `cdj-selected-export-2026-11-03/` and
`theme-cursor-2026-11-03/` respectively. These are private local evidence paths.

Sonny is playing with this update; wrap-up is publication authorization, not
physical-device acceptance or permission to interrupt his session. One temporary
rollback stays until he accepts the update:
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-export-update-install-zvxfrqm0/Soundbox-previous.app`
(runtime b0ccb1ab…, executable410196e9…). The two older generated build157
rollback bundles (fc696312… and d5cb4451…) were retired under the standing cleanup
rule after Sonny used/positively accepted build160's Info behavior. Exact paths,
hashes and retention resolution are in `older-rollbacks-retired.json`. Current
build160 was preserved as the new rollback. Native acceptance/recovery/fixtures
and source originals were not part of app cleanup. Do not recreate obsolete apps.
Redundant public/personal dist/COLLECT/PyInstaller outputs were retired after
verified installation:1115613504logical regular-file bytes, exact paths in
`post-install-retirement.json`. Source, venv, native/CDJ/check outputs and required fixtures stay.
[Archived checkpoints](archive/native-analysis-delivery-checkpoints-2026-11-02-03.md)
never authorize rollback, source publication or restoring disposed files.

November4 wrap-up preserves the running app and its current rollback while Sonny
tries the update. All675committed source/test/build inputs match the release
freeze; no additional runtime edit/rebuild/reinstall was made for publication.
The SSH agent had no identities, so Git publication uses the existing authenticated
GitHub HTTPS credential helper for this command only; remote/global preferences
remain unchanged. Exact publication identity is in the private publication receipt.

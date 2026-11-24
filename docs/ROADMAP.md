# SoundBox roadmap

Reviewed November23,2026. [HANDOFF](HANDOFF.md) records the actual source, installed
app, release checks and pending acceptance. This page tracks current work;
[dated release evidence](test-release-gate.md) and the
[earlier roadmap](archive/roadmap-through-2026-11-05.md) retain historical results.
Old pilots and approvals never authorize another source/device action.

## Current delivery

The desktop now uses changed-record canonical saves, worker-owned preparation and
cache/native-reader helpers, prepared browser updates that preserve navigation,
and immediate usable per-song cue overlays. Accepted fast local loading and the
first complete waveform remain release requirements. DB acknowledgement,
source identity, recovery and normal-Quit drains remain independent of immediate
presentation. [Player contracts](PLAYER.md) and [runtime contracts](runtime-contracts.md)
define those boundaries.

The November23 follow-up separates requested Play/Pause from decoder snapshots,
keeps exact seek/cue targets through decoder retry, and binds browser navigation
and loaded Info to source/song occurrences. Scoped artwork owners preserve manual
choices across compatible refreshes and reject late source/ABA results. Current
installed identity and validation are in HANDOFF. Successful update completion
also retires its temporary previous app and completed diagnostic/capture/state
copies, retaining small result receipts under the November23 retention contract.

The accepted November21 player follow-up preserves RGB exactly. BLUE shares its
signed outline, LOD and motion while using native palette families over resident
spectral proxies; exact native BLUE pixel equality is unclaimed. Main 3BAND keeps
its independent PWV7 contours, detail and continuous motion with a 1.35 display
gain. Its seven-color compositor integrates joint pixel coverage. Optional native
PWV4/PWV6 previews prepare before grouped first-frame publication; 3BAND's mini
uses its dedicated stacked preview, independently of the main waveform.

Regular playlists now retain authored occurrence order and support exact-occurrence
track dragging. Durable batches compose the existing accepted removal/append
effects with bounded replay; pending order, duplicate identity, selection and
drafts survive until verified source acknowledgement. Collection and smart lists
keep newest-first defaults. See [playlist ordering](native-playlist-order.md).

The sidebar resizes horizontally; device captions show firmware/type and total
capacity. Bounded read-only discovery supports Video/Classic identity with exact
mount/USB provenance. Observed writable FAT32 iTunesDB/signature profiles govern
writer admission; remembered device/model labels do not grant permission.
Generic USBs keep explicit selected-playlist CDJ export, with no automatic Sync
status or idle catalog comparisons.

The installed iPod export update captures a durable selected generation while automatic
metadata is paused or running, keeps routine metadata progress out of the footer,
and offers Export when there is no live playlist association to Sync. It preserves
selected physical/structural save fences and truthful cancellation/completion.
CDJ policy separation, excluded-suffix rejection and quiet joined Cancel were
delivered. The installed follow-up corrects stale hidden device caches,
optional-association success popups, association loss across separate exports,
Delete/re-export acceleration and metadata-only pending state. It
offers Sync only for observed pending linked-playlist changes and avoids full
payload reads to omit definite unsupported WAVs. Exact current source/
install/check status is in HANDOFF. The current
new-copy iTunes codec profile remains narrower than Classic hardware support;
AAC/ALAC/AIFF expansion and retained dual promotion are separate open work.
Physical first export, reconnect and firmware acceptance remain separate from
disposable fixtures and package verification.

Sonny accepted the installed iTunes deletion/export/navigation and one-added-song
reconnect/Sync workflow on November18. The November19 follow-up anchors shared
progress/Cancel, mirrors Export/Sync across both primary buttons, fixes My Tags
bounds and moves deletion into the exact tag's context menu. It also separates
nonphysical source saves from iPod IO, uses unique leaf playlist names and retains
unchanged source proofs across partial updates. Current validation/install status
is in [HANDOFF](HANDOFF.md); larger metadata coexistence, external-exporter and
Rockbox/dual physical acceptance remain separate user workflow checks.

The November20 follow-up makes newly created tags immediately usable through
exact dependent intentions, preserves literal ampersands in My Tags, adds a
larger bright upper-right sparkle, slightly overlapping each smart playlist's
normal icon (preview A, superseded by bolder B on November21), and keeps the
sidebar divider thin/plain. The later native-save follow-up promotes eligible
metadata wakeups and removes repeated cipher derivation/playlist reconstruction;
fresh source checks and failure recovery remain intact.
Smart/native playlist rename, reorder and cross-folder moves retain independently
accepted exact-library protocols; folder-centre drops append inside either a
collapsed or expanded folder. Guarded new-local tag/smart deletion remains
separate from older/synced history. These changes never rewrite source names or
rules through presentation alone; current evidence is in HANDOFF.

Developer validation now offers named areas and cumulative quick/integration/
recovery depths, including separate core USB/CDJ and iPod areas. Exact selectors
remain available. Small local fixes use affected coverage and guarded package/
install checks; the complete zero-skip gate remains required before publication.
See [the test workflow](test-release-gate.md).

## Shared rekordbox persistence

Sonny requires the current effective SoundBox library to survive in rekordbox:
preserve cleaned titles, artist aliases and organization before native refresh or
publication. The [feature audit](shared-library-audit-2026-11.md) separates accepted
source domains, current implementation and remaining gaps.

- Ordinary native playlist/folder CRUD, membership, sibling reorder, direct
  registration, My Tags and cue/grid edits have independently accepted domains.
- Tag-definition creation and My Tag ANY/OR smart-playlist creation have separate
  exact-library acceptance. Creation does not authorize deletion or existing-rule edits.
- Title/artist saves and migration use the independently accepted metadata profile
  and shared guarded writer. Broader Track Info fields, curated native artwork,
  unsupported album/cloud transitions and other metadata scopes remain open.
- Initial full WAV and narrow CBR MP3 analysis have independent accepted profiles;
  broader codecs, existing-analysis replacement and gain remain separate gates.
- Tag/smart deletion envelopes, older/synced records, intelligent-list exclusions
  and lossless native-only Collection Restore retain their explicit open boundaries.

Local-only presentation and copied/generated proof do not complete an offered native
edit. No sign-in, new online functionality, broad SQL authority or historical
whole-library restoration follows from this work.

## Rolling critical-path review

Keep independent audits running alongside focused releases. Reproduce findings,
record exact affected code and ownership/data risks, then ship separately validated
fixes without delaying an unrelated active release. The
[critical audit ledger](critical-audit-2026-11.md) retains open rollback, artwork,
USB draft and player interaction findings. Passing automated tests never closes
an unobserved hardware path or an incomplete architecture migration.

## Independent acceptance and deferred work

- Broader iTunes metadata-heavy Sync, Rockbox/dual, cancellation and firmware
  acceptance remains open. Preserve unresolved recovery; the accepted current
  iTunes workflow does not establish those independent paths.
- CDJ export maintains both native catalogs; broader player/control/OneLibrary
  hardware acceptance remains open.
- The November21 BLUE palette and independent 3BAND thickness/palette/preview
  follow-ups are accepted. Further tuning follows Sonny's request; preserve RGB,
  complete first-frame readiness, independent 3BAND detail and musical timing.
- Further public splitting/release remains deferred until the personal app is
  finished. Existing profiles remain; dependency/service clearance, notices,
  signing/notarization, clean-machine upgrades and hardware coverage are still required.
- Embedded-art extraction, schema2→3 migration, unsupported stock database/checksum
  families, Windows and full Rockbox ReplayGain remain deferred.

Use [the workflow](shared-library-workflow-plan.md), [devices](DEVICES.md),
[CDJ contract](cdj-usb-export.md) and [distribution boundaries](release-boundaries.md)
for exact scopes. Uncommitted UX proposals stay in [IDEAS](IDEAS.md).

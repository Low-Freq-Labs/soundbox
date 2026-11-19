# SoundBox roadmap

Reviewed November18,2026. [HANDOFF](HANDOFF.md) records the actual source, installed
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
reconnect/Sync workflow on November18. His next-session requests are to anchor the
shared iPod/USB progress controls as phase messages change and reconcile the
lower-left explicit playlist Sync wording with the clean header's Export action.
He also plans a larger tag/metadata Sync test. These are deferred for the requested
wrap; [HANDOFF](HANDOFF.md) preserves the exact observations and implementation leads.

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
- Current waveform appearance was accepted November9. Reopen tuning only at
  Sonny's request; preserve load speed, complete geometry and musical timing.
- Further public splitting/release remains deferred until the personal app is
  finished. Existing profiles remain; dependency/service clearance, notices,
  signing/notarization, clean-machine upgrades and hardware coverage are still required.
- Embedded-art extraction, schema2→3 migration, unsupported stock database/checksum
  families, Windows and full Rockbox ReplayGain remain deferred.

Use [the workflow](shared-library-workflow-plan.md), [devices](DEVICES.md),
[CDJ contract](cdj-usb-export.md) and [distribution boundaries](release-boundaries.md)
for exact scopes. Uncommitted UX proposals stay in [IDEAS](IDEAS.md).

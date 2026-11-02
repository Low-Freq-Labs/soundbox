# SoundBox roadmap

Updated November 1, 2026. [HANDOFF](HANDOFF.md) records the installed binary,
verification and preservation baseline. This roadmap describes current behavior
and remaining work. Older release hashes, test counts and original pending gates
are [historical checkpoint evidence](archive/roadmap-checkpoints-through-2026-11-01.md).

## Delivered personal runtime

The installed personal build passed all2035 offscreen/Cocoa tests, signed-package,
safe-install and actual-source readback checks. It includes:

- Shared Collection/native-playlist imports with stable identities, durable
  destination routing and ordinary native playlist CRUD. Analysis can be off for
  bare registration/membership. Refresh isolates native deletions per identity.
- Automatic initial full BPM/Grid+Key/native waveforms for the separately accepted
  bare stereo PCM16/24 WAV44.1/48kHz profile. Existing saved results are reused;
  native/manual musical data is preserved. Native publication waits for closed
  rekordbox/agent and exact source authority.
- Durable native/import cue/grid capture during unrelated work, exact v2 grid
  arithmetic and receipt reconciliation. The previously stuck53 intentions now
  match four final native grids and16 cues, with no pending prep queue at delivery.
- Clamped boundary jumps, held mouse/keyboard repetition, consistent BLUE/3BAND
  middle-zoom height and user-approved gentle wide RGB colors. Prior accepted
  close-zoom rendering and musical clocks remain protected.
- Inline hex colors and sidebar rename, fixed-green PLAY/orange held CUE with no
  hover state, corrected/padded SB icon and saved-accent persistence after quit,
  freely draggable rightmost columns and trackpad scrollbar feedback.
- Graveyard search/info/storage, position-preserving Restore and confirmed
  Collection/disk Delete with recoverable Trash/shared-file protection. Sonny
  confirms bulk Delete works;515 disposal receipts are current state. Four absent
  identities offer explicit local-only dismissal through the same user button.
- Automatic recoverable compatibility conversion and existing-record locator
  updates.124 WAV conversions completed with exact originals retained. Combined
  Analyze rebuilds waveforms; artist-profile artwork is available above Remove.

These are completed features, not new native-reference requests. User interaction
with the latest controls remains Sonny's own live testing; the agent must not
send input events into his app or substitute synthetic proof for native acceptance.

## Shared-library follow-up

The native database/sidecars are the shared musical authority; intentions and
captured detector records are provenance, not a competing final musical store.
See the [implementation plan](shared-library-workflow-plan.md).

- Broaden initial-analysis codec/component profiles only with independent native
  evidence; existing-native-analysis replacement, gain and broader metadata are
  not accepted by the initial WAV profile. Intelligent/cloud edits, tag-definition
  management, Undo/Redo and title/artist write-back remain separate scope.
- Review remaining old imports without bulk identity adoption or reanalysis of
  preserved songs. Existing independent logical/native curation requires review.
- Resolve unsupported compatibility cases only with verified timing/layout and
  recovery. One32kHz MP3 is unchanged/timing-unproven; cloud-deferred/unavailable
  files are explicit. No claim that every Collection file is universally verified.
- Preserve exact conversion originals, user cues/grids, native memberships and
  disposal receipts; never restore an old whole library to repair one component.

## Device acceptance and performance

Rekordbox and SoundBox are equal exporters. Preserve the current native USB
catalogs/media across either application's writes. Dedicated CDJ publication is
implemented for classic Device Library plus Device Library Plus; playlist drops
start directly. Sonny reports playback, artwork and waveforms working. The full
CDJ-3000 control matrix, newer OneLibrary hardware and wider physical coverage
remain open. See [the CDJ contract](cdj-usb-export.md).

Stock/dual/Rockbox follow-up still requires real reconnect/power-cycle, playing
music during discovery, no-op/changed Sync, overlapping drops, cancel/retry and
normal eject checks. Preserve generation-58 recovery until separately reviewed
acceptance. Normal idle eject does not require Sync/Verify. Generic USB/Rockbox
optimization remains deferred; measure actual source/device reads, staging peak
and latency before claiming further performance gains. See
[device guidance](DEVICES.md), [lifecycle evidence](ipod-lifecycle-audit-2026-09.md)
and [sync measurements](sync-performance-audit-2026-09.md).

## Architecture and distribution

Public/personal profiles and typed optional-provider boundaries are implemented,
with shared core/state/device guards. Public excludes the current SoundCloud
implementation and yt_dlp. Dependency/license/notice review, signing/notarization,
clean-machine install/upgrade, permissions failures and supported-hardware matrix
remain public-release gates. [Release boundaries](release-boundaries.md) govern
composition; no public clearance is implied by the personal install.

Continue owner/controller extraction only as maintenance warrants: library/cache/
curation orchestration, device/artwork ownership, playback navigation and exporter
planning/transactions. Preserve firmware-specific recovery order and APIs rather
than performing a universal exporter rewrite. Remaining coordinator queue/GUI
filesystem work needs behavior parity and measured evidence.

Read-only embedded artwork extraction, schema2→3 migration, additional stock
models/formats, Windows and complete Rockbox ReplayGain remain deferred, each with
its own preservation/acceptance requirements. Exploratory UX stays in
[IDEAS](IDEAS.md). No speculative rewrite or new user acceptance task is required
to finish this delivered session.

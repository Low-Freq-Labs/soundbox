# SoundBox roadmap

Updated November 3, 2026. [HANDOFF](HANDOFF.md) records the installed binary,
verification and preservation baseline. This roadmap describes current behavior
and remaining work. Older release hashes, test counts and original pending gates
are [historical checkpoint evidence](archive/roadmap-checkpoints-through-2026-11-01.md).

## Delivered personal runtime

The November3 personal build155 delivery passed all2341 offscreen/Cocoa tests, signed
package/native smoke, safe-install and curated-state checks. Exact binary/source
provenance belongs in HANDOFF. It includes:

- Reference-based clean crate outline with an open rim/handle, straight sides and
  flat bottom; saved playlist choices, theme and row geometry are preserved.
- Captured waveform→BPM→key preparation precedes new native DB work after required
  conversion, with durable component checkpoints and quiet exact-byte ctime proof.
  Actual Mocha145/9A/425-beat source recovery passed. Memory Cue recall moves the
  regular temporary CUE anchor; duplicate confirmation has a compact wrapped layout.
- Completed background waveform BPM estimates appear before loading, with an
  explicit estimate tooltip, native/detector/manual precedence and stale-identity
  rejection. They confer no source authority. Manual grids over inconclusive
  detector results reload correctly; current installed provenance is in HANDOFF.
- Shared Collection/native-playlist imports with stable identities, durable
  destination routing and ordinary native playlist CRUD. Analysis can be off for
  bare registration/membership. Refresh isolates native deletions per identity.
- Automatic initial full BPM/Grid+Key/native waveforms for the separately accepted
  bare stereo PCM16/24 WAV44.1/48kHz profile and the separate MPEG-1 CBR/no-Info/Xing
  stereo44.1kHz MP3 profile. Existing saved results are reused;
  native/manual musical data is preserved. Native publication waits for closed
  rekordbox/agent and exact source authority.
- Durable native/import cue/grid capture during unrelated work, exact v2 grid
  arithmetic and receipt reconciliation. The previously stuck53 intentions now
  match four final native grids and16 cues, with no pending prep queue at delivery.
- Clamped boundary jumps and held mouse/keyboard repetition; calibrated
  BLUE/3BAND height and a fixed92pt/s RGB reference palette across all13zooms.
  Complete waveform first paint avoids AAC/ALAC preview resizing, with lower
  cache/read/upload costs and preserved geometry, color and musical clocks.
- One native Playlists tree, newest-added-first browser views and accepted native
  sibling reordering, with Collection/virtual Artists fixed at the top. The
  requested one-time legacy Collection/custom merge is complete; Artists creates
  no native records or ongoing jobs. Playlist-only Star/Music note/Vinyl crate
  choices persist locally; Artists uses a fixed person icon.
- Stable selection, scroll, folder expansion and Edit Tags geometry across native
  updates. Native analysis preserves adaptive tempo changes; errored songs do not
  stall healthy imports. Ctime-only prep drift keeps gestures durable during an
  exact-byte worker check. UI Quit stays open while captured saves finish, with
  editing and save pumps active.
- Inline hex colors and sidebar rename, fixed-green PLAY/orange held CUE with no
  hover state, corrected/padded SB icon and saved-accent persistence after quit,
  freely draggable rightmost columns and trackpad scrollbar feedback.
- Graveyard search/info/storage, position-preserving Restore and confirmed
  Collection/disk Delete with recoverable Trash/shared-file protection. Sonny
  confirms bulk Delete works;515 file-disposal receipts and four user-confirmed
  local dismissals are current state. Absent identities offer explicit local-only
  dismissal through the same user button.
- Automatic recoverable compatibility conversion and existing-record locator
  updates.124 WAV conversions completed with exact originals retained. Combined
  Analyze rebuilds waveforms; artist-profile artwork is available above Remove.

These are completed features, not new native-reference requests. User interaction
with the latest controls remains Sonny's own live testing; the agent must not
send input events into his app or substitute synthetic proof for native acceptance.

## Shared-library follow-up

The November2 dedicated per-command review covered every native writer plus shared
primitives. Its measured process/queue/cue/grid/registration/locator/backup/XML gains
remain included in the current November3 delivery. Measurements describe private
copies, not actual gesture latency. The November3 local preparation order and
metadata recovery are installed; exact current validation/provenance is in
[HANDOFF](HANDOFF.md), with the detailed workload limits in
[performance](performance-audit.md). Source/authority/payload/recovery guards stay
fresh. Sonny is now testing the running app; no new automatic job or rollout is
pending for the agent.

The native database/sidecars are the shared musical authority; intentions and
captured detector records are provenance, not a competing final musical store.
See the [implementation plan](shared-library-workflow-plan.md).

- Broaden initial-analysis codec/component profiles only with independent native
  evidence; existing-native-analysis replacement, gain and broader metadata are
  not accepted by the initial WAV profile. Intelligent/cloud edits, tag-definition
  management, Undo/Redo and title/artist write-back remain separate scope.
  The [MP3 follow-up](mp3-shared-analysis.md) passed isolated native references,
  explicit CBR source-pilot approval and native load/normal-quit acceptance.
  Its independent CBR authority is installed; legacy WAV scope stays exact.
  VBR seek research is byte-exact, but VBR/Info/gapless/mono/48kHz MP3 publication
  remains a separate gate.
- Resolve unsupported compatibility cases only with verified timing/layout and
  recovery. One32kHz MP3 is unchanged/timing-unproven; cloud-deferred/unavailable
  files are explicit. No claim that every Collection file is universally verified.
- Preserve exact conversion originals, user cues/grids, native memberships and
  disposal receipts; never restore an old whole library to repair one component.
  The discontinued generated artist tree is removed; retain its exact recovery
  evidence and never recreate it. Current user state supersedes migration counts.

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

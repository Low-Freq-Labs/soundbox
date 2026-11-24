# SoundBox current handoff

## Current delivery — November 23, 2026

Branch: `main`. Sonny requested complete documentation review, validated cleanup,
commit and push, then reported late Hot/Memory markers on the mini waveform.
The cue fix is installed and passively verified. The publication commit is the
current `git log -1` after wrap-up; final validation is recorded below.

## Installed application

`/Applications/Soundbox.app` is signed personal build 174, native ABI 9, ordinarily
reopened at 00:31 UTC November 24 after Sonny confirmed normal Quit. It was built
from dirty base `91a60c3432f9e8809dd12b61582e5044edf03082`. Runtime fingerprint:
`ecd45b50abe3dc918b32942f2b620dd16e2fc4a113958daca8713299bccff16f`.
Executable SHA256:
`86f1273f2c379236e99652727fbe95be5a010bf010aa5dd224acd59d21fed763`.
A later publication commit does not restamp this binary; exact signed composition
and the runtime fingerprint bind it to the tested source.

- Mini Hot/Memory markers appear on the first complete waveform frame using a
  display-only resident timeline until decoder duration is ready. Decoder
  timeline publication wakes paused rendering. No new load wait or source read.
- BLUE shares RGB's exact main outline/LOD/motion and uses the native palette
  over resident PWV5 spectral proxies; exact native BLUE video parity is unclaimed.
- 3BAND keeps its independent PWV7 contours/detail, continuous motion and 1.35
  main gain. Joint encoded pixel areas correct overlap colors. Its mini uses
  dedicated PWV6 data and the native 1200×25 stacked preview.
- Optional PWV4/PWV6 previews precede complete grouped publication. No new DSP,
  waveform-cache format, migration or recooking belongs to these display fixes.
- Regular playlists retain authored occurrence order and native track dragging
  through the accepted remove+append composition. Collection/smart defaults stay
  newest-first; exact source, recovery and acknowledgement guards remain.
- Complete selected artwork precedes Info's first frame; complete waveform
  readiness gates audio. Latest selection, transport, drafts and durable saves win.

See [PLAYER](PLAYER.md), [playlist order](native-playlist-order.md) and
[runtime contracts](runtime-contracts.md) for behavior and authority boundaries.

## Validation

The complete bare `./check.sh --render` passed: all 5,285 discovered tests ran
exactly once, with 5,214 offscreen and 71 Cocoa cases, zero skips and zero expected
failures. Lint, folder guides/links, instruction parity, engine types, required
fixtures and 12 Rust tests passed. The fresh synthetic UI render was inspected.
All 978 frozen runtime/test/build inputs remain exact. HANDOFF release-result
metadata is finalized separately with layout/link checks and a per-file receipt.

Actual first-frame Metal pixels show Hot and Memory mini markers with decoded
duration still zero; replacement clears them without inventing audio readiness.
Decoder wake has source/lifecycle review rather than a dedicated delayed-decoder
oracle. Full native playback, grouped readiness and cadence checks passed.
The accepted Metal source is unchanged by this cue fix. Earlier generated
comparisons retain 624 RGB/BLUE frames/attribute cases and 312 independent 3BAND
attribute cases against accepted 9ed4; whole-video/device equivalence is unclaimed.

Strict signed composition verified 238 runtime/startup modules, four native code
sections/payloads, ABI 9 and the full exact Metal literal. Isolated packaged native,
process-query, SQL/ORM and AES checks passed without live-source/device discovery.

The documentation audit covers 120 documents and 22 dependency/toolchain manifests:
all 65 current first-party documents received full-content review, 28 archives
received scope/permission/link review, and all 27 upstream docs/licenses match
their pin. Cue/ABI deltas received a further review. Local targets/anchors passed;
58 intentionally omitted upstream references are documented in project layout.
External links, new hardware acceptance and legal clearance are not claimed.

## State and retention

Fresh canonical/record/portable backup preceded installation. Startup loaded
3,298 tracks and 86 playlists, with zero preparation tasks or artwork fetch/fail.
The backup holds 6,433 records and current curation/queues, saved RGB mode and white
icon. Passive verification at 00:41 UTC matched the loaded executable/native
identities, exact canonical curation/queues, all records and saved preferences.
Five derived cache/provenance observations were independently reviewed and
retained. Current preparation signatures reconstruct exactly; causes of seven
older opaque signatures remain unproved. These are scheduling hints, not source
authority. No old state was restored. Newer user edits supersede dated counts.
Duplicate personal distribution/packaging outputs were retired after exact
signature, Contents and receipt checks; the pending previous app stays intact.

Delivery receipts: `~/Library/Logs/Soundbox/cue-readiness-20261123/`.
Documentation/gate receipts: `~/Library/Logs/Soundbox/wrapup-20261123/`.
Earlier waveform reference proofs: `~/Library/Logs/Soundbox/3band-reference-20261121/`.
The exact accepted 9ed4 rollback was retired during the guarded rotation. Keep
only the immediate previous 7a5c app at `cue-readiness-20261123/install/previous/`
until Sonny accepts this new cue delivery. It is pending acceptance, not garbage.
Preserve AppData/records, current recovery, originals/conversions, required offline
fixtures, active `.venv` and native/check/CDJ release outputs. No general music,
AppData or Trash cleanup is authorized.

MainWindow/controller coupling and large iPod planners remain organization debt.
Complete-grid encoding/GUI acknowledgement and measured projection/storage costs
remain open. This is not an exhaustive dead-code or instant-every-function claim.
See [critical findings](critical-audit-2026-11.md), [performance](performance-audit.md),
[roadmap](ROADMAP.md) and [project layout](PROJECT_LAYOUT.md).
[Earlier dated handoff](archive/handoff-2026-11-23-before-wrap.md) retains its original
scope and never grants new app/source/device actions.

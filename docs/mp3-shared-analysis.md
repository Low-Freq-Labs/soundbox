# MP3 shared-analysis follow-up

Started November 2, 2026 at Sonny's request. **The accepted CBR profile remains
installed and enabled in personal build160/source release `4a3d29a`.**
Its original production delivery was build155; the dated checkpoints below retain
that history. It covers initial full BPM/Grid+Key/waveforms
on bare unlocked MPEG-1/no-Info/Xing CBR, stereo44.1kHz. VBR, Info/gapless, mono
and48kHz MP3 publication remain gated. The separate WAV v1 scope is unchanged.
Current runtime provenance stays in [HANDOFF](HANDOFF.md).
Leave the installed app running; historical closed-app work instructions below
are not current permission to stop it, repeat a pilot or add generated songs.

## Implemented preparation

The v1 `AnalysisAuthority` now explicitly exposes its immutable accepted WAV
profile. Independent guards check it before rendering, when reconstructing a
saved plan and at source approval. Its serialized fields and existing recovery
plans are unchanged. Adding an MP3 or unknown profile to v1 authority JSON is
rejected. Neither research evidence nor a renderer that can decode MP3 widens
that authority.

The pure scanner and CBR planner now live in `soundbox.mp3_scan` and
`soundbox.native_mp3_analysis_plan`; research compatibility wrappers retain their
original exact plan outputs. Runtime packages never import research code.
The independent production `rekordbox_cbr_analysis_store` has its own authority,
plan/journal formats, archive prefix and receipt-backed bounded retention on the
existing source worker/root/lock. New jobs persist an explicit profile; old jobs
remain WAV v1. Ctime-only renewal uses a worker SHA proof and preserves the
original durable result, while native/manual existing analysis is never replaced.

Three audit tools operate on explicit files, never a source database:

- `research.native_library.mp3_seek` scans bounded stereo MPEG-1 Layer III
  44.1/48kHz captures, reconstructs only the observed CBR-without-Info/Xing seek
  section and compares native VBR offsets to actual audio-frame boundaries.
  Unknown tags/framing, changes, truncation and budgets fail explicitly.
- `research.native_library.mp3_timing` keeps header duration, existing playback
  origin, renderer frames and native packet/grid/detail counts separate. Exact
  rational differences remain visible, including tiny header rounding errors.
  It proposes no universal MP3 delay or endpoint adjustment.
- `research.native_library.mp3_analysis_audit` captures one explicit MP3 into
  TEMP, supplies its frozen native grid to the existing renderer and compares
  the fresh packet with all three native packets. It binds filenames, hashes
  and stable input epochs, refuses mounted-device inputs and exclusively creates
  a new report outside the protected reference directories. Native seek bytes
  are compared, never silently copied into the generated packet.

These modules are research code excluded from app packages. They contain no SQL,
source publisher, authority provisioner, audio retagging or compatibility conversion.

## Frozen evidence

The existing generated-format captures under the private
`~/Library/Logs/Soundbox/shared-native-library-2026-10-30/` root include native CBR
and Xing VBR MP3s. The new compact reports are under
`~/Library/Logs/Soundbox/mp3-analysis-2026-11-02/`.

Both references contain 1,226 audio frames / 1,412,352 untrimmed samples at
44.1kHz. Native detail has 4,804 columns, with 64 grid beats from 274 to 31,774ms.
Fresh rendering preserves every supplied grid row and matches all three detail
counts. Its seek section differs: the existing helper authors all-zero PVBR.

The independently reconstructed CBR PVBR is byte-exact: 400 zero offset words
followed by the untrimmed sample count. The native VBR's 400 offsets all point
to audio-frame boundaries relative to byte228, after the Xing frame. The new
independent shorter native reference now also matches every bucket: select audio
frame `max(0, floor((i+1)*audio_frames/400)-8)` and store its byte offset relative
to the first audio frame. Research-only `vbr_pvbr` reproduces both complete native
sections byte-for-byte. It rejects unproven Info, tagless/constant-rate Xing and
48kHz generation profiles; no production MP3 permission follows.

Native MP3 technical fields also differ from the WAV policy: BitDepth16, Length32
and first-audio-frame bitrate (320 CBR / 224 VBR in these references). The VBR
average bitrate is not that native field. Header duration and renderer duration
are recorded separately, even when their difference is only decimal rounding.
Other generated Info/Xing examples can differ materially; never substitute header
seconds or apply one universal lead offset.

A copy-only CBR packet candidate is retained in the new private root's
`cbr-packet-candidate/`. It uses the current detector's saved120BPM/8B grid
(64beats, first273ms), freshly rendered waveforms and independently constructed
CBR seek bytes. Its exact saved grid and 1,412,352-frame endpoint are verified.
The later source pilot and acceptance are described below. Current detector
records for all three staged clips are retained separately in `detector-results/`;
the short VBR has38beats and a19.173877551-second rendered song clock.

The old native analysis batch interleaved eleven files. New isolated native
captures now prove each MP3's +20 counter sequence: mixer+13, target+17 and files
`.2EX/.DAT/.EXT` at+18/+19/+20. Only target content, three file registrations,
one mixer and localUpdateCount change;38other tables, XML, schema and metadata
remain exact for each action. Native generated packets have
a populated secondary grid and gain rows; MP3 acceptance of the WAV component
policy (no gain row, invalidated secondary grid) was separately accepted by the
CBR source pilot below. The VBR component policy remains unaccepted.

## Native reference and acceptance

Three generated clips are prepared in the new private root's `reference-audio/`:
CBR without Xing, the existing Xing VBR and a newly encoded shorter Xing VBR.
Their exact source digests/sizes are in `reference-audio-manifest.json`. The short
clip supplies independent duration/bitrate evidence for VBR bucket research.
These are disposable generated test audio, not copies of Sonny's music.

The completed November2 native-reference procedure was operated by Sonny,
as required by the project instructions. It is retained as history, not a new task:

1. Import the three clips into rekordbox with automatic analysis off; leave them
   unanalyzed. Capture a stable read-only baseline after native startup/import.
2. Analyze only the CBR clip for BPM/Grid+Key, with first-beat Memory Cue off.
   Capture its complete target/counter/file/mixer delta and preserve unrelated
   semantic inventories, XML and source hashes.
3. Repeat one clip at a time for each VBR reference, with a capture between each.
   Do not reanalyze existing musical tracks.

Sonny completed the three bare imports. The verified read-only
`before-cbr-analysis` baseline retains native IDs159372012(CBR),108982934(VBR)
and186527498(short VBR), all Analysed0/BPM0/KeyID0 without analysis paths.
Sonny then analyzed each clip separately. Captures `after-cbr-analysis`,
`after-vbr-analysis` and `after-short-vbr-analysis` retain the isolated results:
counters368090→368110→368130→368150. The short native VBR has38beats,
274–18774ms, and key5A; the SoundBox detector chose8B for that clip. Format/write
protocol evidence does not claim matching key-detection algorithms.
These user-created native
test entries supersede the earlier library-count baseline; never restore an old
library to remove them.

The pure `mp3_analysis_plan` proposal and encrypted-copy replay independently
reproduce the CBR target metadata and +20 counter exactly, while preserving all
unrelated rows. Its own detected grid is273ms, with no gain row and an empty
secondary grid. The separately scoped `mp3_analysis_pilot` reuses the proven
transaction/recovery transport but has its own exact-plan approval and journal
format, fixed generated SHA/name/grid, fresh closed-app checks and shared root/
lock. It does not borrow the WAV authority or provision production authority.
Seven synthetic adapter tests include four actual process-exit recovery prefixes.
Sonny imported the separate generated `SoundBox MP3 CBR candidate.mp3` with
analysis off and quit both apps normally. A normal SoundBox quit request finished
its remaining shutdown; no process was forced. Copy-only preparation is complete:
native ID41293771/UUID094781a2-977a-47ae-b4d0-81733870024e,
operation9dde8776-d5c0-4757-b0ca-5e7462072441, plan SHA-256
`4e9afac50f8a0d8d2caa5ca5952ef149269a27ccde24413c6d8bb41d13a890cb`.
The plan and compact `pilot-review.json` are under the new private root.
It proposes120BPM/8B/64beats273–31773ms, three file registrations and counter
368151→368171; only djmdContent/contentFile/agentRegistry change. The exact
encrypted pair/XML/packets are verified, destination absent, audio intact and
current crown-state/authority hashes unchanged before approval.

Sonny explicitly replied **approved** to that exact one-track pilot. Publication
committed through the shared lease with encrypted recovery retained, complete
after-inventory equality and native row/grid/packet readback. He then confirmed
**looks good!!!** after native load, opening/middle/tail inspection and normal
quit. `native-accepted-cbr/acceptance.json` proves the target, every grid row,
DAT/EXT/2EX, audio/XML and all non-registry tables remained exact; five native
startup registry housekeeping changes remain explicitly recorded. Counter368171
stayed unchanged. Its acceptance SHA-256 is
`48a2134d188351bd9e5b2a2a6da027d1f6b00975bbaa46e99add489f8203adea`.
The pilot is terminal; never replay its approval/source helper. Its full recovery
archive and original `pilot-tested-source/` remain protected.

That proof provisions only the independent CBR library/schema/version authority
at `rekordbox-cbr-analysis-authority.json`; the9existing authorities remain exact,
for10total. It does not grant VBR or metadata/replacement permissions. CBR advances while
VBR component acceptance remains separate. Info/Xing/gapless and 48kHz profiles must not
inherit acceptance from the no-Xing 44.1kHz CBR reference.

The Collection's existing timing-unproven 32kHz MP3 is excluded from this work.
Existing native analysis replacement, gain, partial components and metadata
write-back remain outside the initial-analysis scope.

## Validation

The first preparation slice's bare `./check.sh` passed all2164cases:2100offscreen in483.693s plus
64Cocoa in67.243s, zero skips/expected failures. Lint/types, native builds, Rust
tests and required offline fixtures passed. Frozen code/helper hashes remained
unchanged throughout the final run. An earlier discovery run rejected a changing
test inventory; a second run was interrupted for the audit-status correction.
Neither is recorded as a passing gate. Compact evidence is retained under the
new private root; those were preparation-stage passes.

The expanded November2 pilot-stage bare `./check.sh` passed all2178cases:2114offscreen in
500.178s plus64Cocoa in67.790s, zero skips/expected failures, with lint/types,
native/Rust builds/tests and required fixtures passing. It includes the CBR row
proposal, exact source-pilot adapter and independent VBR reconstruction. Review
also added strict JSON-boolean approval and rejection of every grid draft in the
fixed pilot. `pilot-validation.json` binds final code hashes, the exact plan hash,
closed apps, unchanged source baseline/crown state and all9existing authorities.
Those checks preceded the explicitly approved source pilot; the source receipt
and native acceptance are retained separately.

The initial CBR production `./check.sh --render` passed2200cases:2136offscreen479.024s
plus64Cocoa67.962s, zero skips or expected failures. Types cover103core files.
Fresh strict signature, native code-section parity, all151first-party package
modules compared to source, no research/test PYZ modules and isolated native smoke
passed. Build155/runtime fingerprintd9e5f2d5… was installed at that checkpoint;
signed Contents,
native startup, saved white icon, current curation and9old+1new authority verify.
One metadata-copy attempt restored154safely; the retry used the platform helper
and one transient rollback root, with no library rollback or forced termination.
Exact installation/package/state reports are in `production-delivery/`.

Sonny completed the ordinary Collection drop of the separate generated
`SoundBox MP3 app import check.mp3`; its durable receipt and exact readback
are verified below. The agent performed no UI import.

Hermetic regressions cover scanner corruption/budgets/aliases, explicit clocks,
packet association and input preservation, report exclusivity, and unchanged
legacy authority/plan replay. Matching grid/seek bytes cannot hide incompatible
waveform dimensions or renderer clocks in the audit exit status; header decimal
rounding alone remains informational.

## Installed Collection check and performance follow-up, November2

Sonny completed the installed file drop and reported successful behavior with slow
adding/updating. A fresh read-only snapshot proves committed native581972561:
120BPM/8B,64beats273–31773ms, exact target/contentFile rows, three packet hashes
and original audio. Operation45a4b2b1-e484-43b5-9c7b-5e22a249c0a7 is terminal.
The resulting95imports/11analyses/93bindings superseded that preinstall baseline;
queues were empty at the check. These are historical counts, superseded by newer
user state and HANDOFF. The small receipt is in
production-delivery/installed-import-check-readback.json.

The then-active follow-up measured native command latency using existing private
DB copies while Sonny kept SoundBox closed. That work and closed-app instruction
are historical; the updated build160 is running for his use. The performance work
did not broaden the accepted MP3 publication scope.

The subsequent native-command performance package was installed in personal155
with runtime fingerprint8882e36f… (distinct from the earlierd9e5…CBR delivery).
All2219tests,152packaged first-party modules, native code/data section parity,
signature and installed isolated CoreAudio/Metal checks passed. All11analyses,
93bindings and10authorities were preserved. Sonny requested SoundBox remain
closed at that checkpoint, so that package had no normal curated launch/UI latency
measurement.
See HANDOFF and the performance audit for its exact scope and remaining costs.

The subsequent second native-command performance delivery was installed at22:56
in personal155, runtime33689af7…/2266tests/154Python modules. All three native
binaries matched tested code/data; installed isolated native player/process checks
passed. Full private CBR workflow then measured12.030s (fixture renderer, no detector
or GUI). At that checkpoint11analyses/93bindings/10authorities and newer UI state were exact.
The real app remained closed at that checkpoint. Exact current provenance and
per-command limits are in HANDOFF/performance audit; no MP3 scope is broadened.

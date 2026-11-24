# Existing Collection media compatibility

November1,2026 implementation/reference record, reconciled November5 against the
responsiveness delivery, personal build167/runtime `3fb0ff60…`; current installation and outstanding cases are in
[HANDOFF](HANDOFF.md). Sonny requested an automatic format check for the entire Collection
and future additions, with recoverable replacement in place so files work on both
the original CDJ-2000 and CDJ-3000. This is authorized semantic scope. The file-only automatic WAVE/unregistered-lossless controller is installed
and provisioned after native WAV acceptance.124 WAV conversions completed with
exact originals retained; HANDOFF records installed provenance and remaining cases. A separate locator/type publisher now implements
existing-record DB updates; the two FLAC reference links are repaired under
Sonny’s explicit instruction. Native locator acceptance and its separate general authority now pass.
The supported automatic workflow is installed; do not infer universal format
coverage or physical-CDJ acceptance. One32kHz MP3 remains timing-unproven and
unchanged. The November1 audit's88 cloud-deferred files and unavailable paths were
unchecked at that checkpoint; those counts are historical, not a fresh live scan.

## Common player profile

Preserve compatible files. Uncompressed integer PCM WAV/AIFF at16/24-bit and
44.1/48kHz overlap both players. MPEG-1 Layer III and AAC LC at44.1/48kHz use their
published bitrate limits. The original2000 does not list FLAC/ALAC;3000 does.
32-bit float WAV is outside the published file profiles, even though the3000's
internal processing uses floating point. Sources:
[original CDJ-2000 manual, page8](https://jpn.pioneer/ja/support/manual/manual_pdf.php?m_id=6619),
[CDJ-3000 official specifications](https://www.pioneerdj.com/en/news/2020/cdj-3000-professional-dj-multi-player/).

The conservative canonical PCM-header case is separate from a demonstrated player
failure: the manuals do not specifically establish WAVE_FORMAT_EXTENSIBLE
compatibility. Canonicalizing those files preserves every audio payload byte and
ordered non-format chunk, verified independently. Do not imply these68 files have
all failed on hardware.

## Read-only Collection audit

Private evidence: `~/Library/Logs/Soundbox/collection-followup-2026-11-01/`.
The audit reads a stable private encrypted DB/WAL snapshot and actual bounded media
headers, skipping dataless cloud files rather than silently counting them healthy.
Native placeholders and Graveyard-only paths are excluded from conversion targets.

Initial active Collection results:3071 header-compatible records;53 float WAVs,
five high-rate PCM WAVs, two FLACs,68 extended PCM-header candidates and eight
unavailable/missing entries. A second pass accounts for83 additional distinct
visible SoundBox imports (81 M4A, one WAV, one MP3); all were dataless
cloud files at that capture and remained explicitly unchecked. No mass hydration was started.
This is a format audit, not an every-sample corruption
scan or physical-CDJ acceptance. Original imported/cue/grid identities remain.

`soundbox.cdj_compatibility` currently prepares separate verified PCM24 copies,
normalizes extended PCM headers without touching their samples, and resamples
88.2→44.1/96→48kHz with CoreAudio's best converter. No gain normalization or detector
rerun occurs. Same-gain over-range peaks clip under Sonny's auditioned policy.
Unsupported layouts/unknown headers remain explicit; no guessed downmix.

## Concrete native references

Four conversion references were verified. Original conversion audio and required
native resources remain protected; completed diagnostic copies follow the current
[retention contract](PROJECT_LAYOUT.md), including November23 capture retirement:

- Kaivon66027819/UUID9877688c-b93b-49eb-a386-fdd67a40baf6: float→PCM24/44.1,
  7,664,276 frames, eight cues; same rate, gain and frame timing.
- Ichor9368146/UUID2e63006b-50c7-4fe4-9dec-9006ace189f4: PCM16/96→PCM24/48,
  7,570,286 output frames, three cues; same duration/gain. Resampling overs clip
  on1740 samples; exact original retained.
- DIGITS12592463/UUIDd4ff75ff-e2bb-4510-8e9f-58226c771a4d: FLAC24/44.1→WAV24/44.1,
  9,162,269 frames; independent sample readback is exact.
- I Can’t Stop138854425/UUID0adb728d-da8b-4be6-8830-98707d3741aa: FLAC24/44.1→WAV24/44.1,
  8,820,000 frames; independent sample readback is exact.

The first two WAVs were atomically replaced at their original canonical paths
under the common native lock after normal SoundBox quit and fresh closed-native
checks. Exact originals and recovery journals precede replacement. The source DB,
XML, cues and all native resources remain byte-identical. No source SQL publisher
was invoked. `audio-reference-publication.json` binds both before/after hashes.

Sonny confirmed both WAVs load with correct grids and clocks, then quit normally.
Their native cached technical columns stayed unchanged. All packets remain exact;
newer user Kaivon cue edits are preserved. This accepts file replacement, not a
native technical-metadata rewrite. The FLAC locator correction is documented below.

## Accepted file-only pipeline

`media_conversion_store` never opens source SQL or edits native packets/XML. Its
own exact library/schema/version/reference authority permits desktop WAVE
replacement and unregistered lossless-to-WAVE materialization. Each intent is
saved before admission to the existing desktop source worker. Exact originals,
staged output and immutable plan precede the common recovery journal. Before/
after audio hashes define recovery; foreign occupants fail closed. The November4
recovery correction binds the existing single digest pass to before/opened/
closed/after inode/epoch checks and rechecks that exact source epoch after the
fresh lease guard before replacement, linking or displacement. Changes retain
the original, private stage and journal instead of overwriting newer audio.
Codec changes
retain their original and publish a same-folder WAV without overwriting a name
collision. Registered cross-codec changes require their separate locator/type
authority. The narrow named-track pilot is provisioned; the general native locator profile is now provisioned after acceptance. New shared imports finish conversion and acknowledge current
analysis/grid/cue drafts/path/destination before registration.

Real WAV acceptance is complete. Sonny confirmed both load at24-bit and their
correct44.1/48 clocks, then quit normally. All native grid/waveform packets remain
exact; newer native Kaivon cue edits are preserved. Reload Tags/deck load leaves
cached master.db technical columns unchanged, so the file-only pipeline preserves
those rows and projects verified, physical-epoch-bound header facts into Info and
USB technical DTOs. BPM/key/cues/grid and native identity remain authoritative.

## Direct existing-record locator update

The first native FLAC Relocate check followed old inodes into backup paths; it was
not WAV acceptance. Original bytes remain safely retained. Sonny then explicitly
requested that SoundBox replace files and update the DB itself, rejecting manual
Relocate. The new `rekordbox_locator_store` uses its own authority, encrypted
paired evidence, shared lease/journal and narrow SQL allowlist. It changes only
FolderPath/FileNameL/FileType/rb_file_id plus per-row/local counters and timestamp.
It preserves track IDs/UUIDs, every musical field, cues, native packets, playlists,
history, tags and XML. Named two-track source publication now passes exact full
inventory and packet preservation; both real records point to their same-folder
WAV replacements, FileType11 and actual WAV inodes. Native reopen/load/normal-quit acceptance preserves both entire
content rows, every packet, XML and all non-registry tables exactly; seven registry
housekeeping changes remain recorded as hashes. No user was asked to approve an unseen plan hash.

The automatic controller finishes file replacement and existing-record location
publication on the same source worker, then acknowledges the latest saved drafts
and rebinds RAM paths. Durable conversion receipts survive process loss before
DB publication. A rolled-back locator intent renews; committed replay preserves
later musical edits. No manual file chooser is part of this workflow. File-only
WAV conversions still make no native SQL changes. Physical CDJ acceptance and
unavailable cloud files remain separate from these format/preservation proofs.

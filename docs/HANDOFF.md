# SoundBox current handoff

Updated November 1, 2026. Branch **main**; this wrap-up commits and publishes the
completed session. Repository HEAD includes documentation and bytecode-identical
EOF-whitespace cleanup after the installed binary was built. Do not claim the binary was rebuilt from that commit.
Sonny is testing the app; leave it running. No automated input into real SoundBox
or rekordbox, no agent music deletion, no new generated library entries.

## Installed runtime and verification

`/Applications/Soundbox.app`, signed personal build153, installed
2026-11-01T20:56:02-06:00. Built from dirty HEAD
`74e2a91a51e2ab94de5075dcfca9c1c0b2e94c29` with source fingerprint
`773a656fa083044579327d81843bb1f14986bb18585e448ba43f9230697c804f`;
executable SHA-256
`eb38953eceac83fb000721cc4b67c45249699233e06f28593c0fd64d65728ff6`.
The final repository source fingerprint is
`7a72f88c3e6a6f1c141a99bee6b16881ba8b7e4c9cb377cddea2c3d3b981050f`.
Only documentation and EOF whitespace differ from the tested installed source;
independent compilation confirms those Python changes produce identical bytecode.
No reinstall is required and the installed provenance remains unchanged.

Bare `./check.sh --render` passed **2035 tests**:1975 offscreen in1232.197s and60
Cocoa in62.988s, zero skips/expected failures, inspected render, first full-run
pass. All55 packaged Python modules match; native sections match the compiled
candidate, with the deck shader intentionally changed and CDJ renderer unchanged.
Assets/ICNS, isolated native smoke, ordinary deep signature, all signed Contents
hashes, safe install and real startup/curation checks pass.

**Actual queued source saves completed:**53 intentions settled, including one
already committed Make Me Feel shift acknowledged without duplicate application
and52 remaining saves through the installed ordinary worker. Four final grids and
all16 cues match exactly; pending prep queue empty and shared journal absent at
verification. All unrelated native tables/rows/target fields,88 protected ANLZ
sections, XML, five pinned audio files, four waveform payload bodies, nine saved
analyses, nine authorities and curation remain exact. Only `pending_prep_edits`
changed in settings.

| Song / logical ID | Native ID | Verified final grid / cues |
| --- | --- | --- |
| Make Me Feel / sb-57e6a6a48ada |201322681|517 rows, first1ms, last247681ms;0 cues|
| BREATHE / sb-6579a2d13608 |2762651569|280 rows;5 cues|
| Innerbloom / sb-19a5565cfb6d |3204654453|721 rows;6 cues|
| WHOLE CLUB ROCKIN / sb-5adee0814145 |3829367246|561 rows;5 cues|

These newer user grids supersede earlier checkpoints. Do not restore BREATHE's
old281-row grid or older cache manifests. Make Me Feel's terminal row legitimately
sits1ms past audio EOF after the user's nudge.

## Delivered behavior

- Direct Collection/native-playlist imports register/reuse stable native IDs and
  durably route destinations. Initial shared full BPM/Grid+Key/waveforms use a
  separate accepted bare stereo PCM16/24 WAV44.1/48kHz profile.
- Ordinary native tracks capture durable cue/grid intentions during source work.
  Prep v2 preserves native terminal rows beyond EOF and verifies real grid receipt
  AFTER rows/DAT/EXT hashes; only cue receipts have `after_revision`. Exact legacy
  proofs reconcile known committed shifts without applying them twice. Unknown
  states stay guarded. Cue capture need not wait for analysis publication.
- Oversized beat jumps clamp to available audio. Mouse/key holds repeat after300ms
  at100ms intervals; release, focus loss or track change stops them. Grid-key holds
  repeat too. Title-cleanup rows no longer select; checkboxes still toggle.
- BLUE middle zooms preserve signed center peaks;3BAND preserves each band's center
  peak. RGB24/32/48/64/92 use25% of measured fine-interval linear-light occupancy
  correction. Sonny approved the third comparison, after rejecting two darker
  candidates. Close RGB transfer/motion/geometry and waveform payloads stay exact.
- Graveyard info/search/storage/Restore anchor and confirmed Delete work. Sonny
  completed515 disposals. Four absent native IDs have no verified paths:
  28390199,131108004,165606422,252495300. Selected alone, existing Delete offers
  explicit local-only stale-entry dismissal. The agent did not dismiss them.
- Recoverable automatic Collection conversion and native locator updates are
  installed.124 WAV conversions completed; exact originals remain. Cross-codec
  conversions update existing records without manual Relocate. Saved white icon
  survives actual normal quit; prior Keychain/shutdown blocker is resolved.

## Preserve and remaining limits

Verified crown baseline:93 imports/1107 title overrides/490 artist overrides/
10 custom playlists/519 hidden tracks, nine saved analyses, nine independent
source authorities and515 disposal receipts. Sonny's newer live edits win. Never
restore pre-bulk library/audio/native state or wipe AppData. Shared VOLATIVE file
for retained native259251743 stays protected. Keep conversion originals, accepted
native references, required offline fixtures and unknown recovery material.

One32kHz MP3 remains timing-unproven and unchanged;88 cloud-deferred files and
unavailable sources are explicit, not certified compatible. Broader shared-analysis
codecs, existing-analysis replacement, gain/metadata and reviewed old-import
migration remain open. Full CDJ control/hardware coverage, iPod follow-up and
public distribution are separate gates; see [ROADMAP](ROADMAP.md).

## Evidence and cleanup

Private latest evidence: `~/Library/Logs/Soundbox/prep-jump-wave-followup-2026-11-01/`:
source freeze, full reports/build/package/installation, `installed-verification.json`,
`native-before.json`, `native-after.json`, expected final components and private
copy-replay verification. The52-operation copy replay used TEMP-only capabilities;
only the installed accepted worker fulfilled real user intentions. Before/rejected/
approved waveform captures live under `wave-preview`; private viewer is closed.
Earlier conversion/native acceptance and white-icon actual-quit proof:
`~/Library/Logs/Soundbox/collection-followup-2026-11-01/`, including
`trash-privacy-fix/after-actual-normal-quit.json`.

Duplicate dist/staging/probe apps and91MiB of obsolete PyInstaller work products
were retired with exact ownership checks; required native builds/fixtures remain.
Documentation audit corrected stale current claims in README, runtime instructions,
roadmap, architecture/player/gotchas, prep/shared-analysis plans and conversion/
Delete guides.184 relative links resolve. The installed source fingerprint remains
773a656f… and executable remainseb38953e…; repository fingerprint differs only
because of the bytecode-identical EOF cleanup described above.
One previous installed rollback is retained while Sonny exercises the new app:
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-float-wave-rollback-olhp3hsm/Soundbox.app`
(previous executable cb9edc73…). Retire it after user acceptance; do not accumulate
more rollback apps. Small backups/receipts and required recovery are protected.

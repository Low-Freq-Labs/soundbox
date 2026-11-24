# SoundBox current handoff

## Release closure — November24, 2026

Branch `main`. The independent documentation review and artwork-decoder correction
are installed and verified in personal176. The running app matches the frozen
runtime fingerprint below. Final Git/remote/clean-worktree proof belongs in
`~/Library/Logs/Soundbox/final-doc-audit-20261124/publication.json`.
Signed provenance retains its actual build-time base; publishing does not restamp
or rebuild an already verified binary merely to change its Git label.

## Delivery behavior

- Review covers and byte-backed Info use a shared native-file QImageReader, avoiding
  the Qt image-plugin/Python QBuffer lock cycle. Captured bytes, exact pixels,
  scaling, cancellation and complete publication remain. Existing paths stay
  direct; byte snapshots add bounded private scratch I/O, measured in
  [UI evidence](ui-polish-audit.md#image-decoder-concurrency--november24).
- Selected artwork is complete before Info's first frame. Source/song ownership,
  manual-choice reservations, compatible refreshes and draft preservation remain;
  old publishers cannot paint onto a newer selection.
- Requested Play survives transient paused/refill observations. Explicit Pause
  cancels queued starts; retry retains the latest seek/cue target and complete
  waveform/cues. Reentrant continuations cannot publish onto newer commands.
- Browser actions use the active visible source/song/occurrence. Rapid navigation
  advances held requests and commits only accepted loads; Artists actions cannot
  target hidden or stale songs. Mini cues remain part of load readiness.
- Regular playlists preserve authored occurrence order; only Collection and smart
  playlists default newest first. RGB/BLUE geometry and independent 3BAND/PWV6
  presentation remain. No native waveform code, shader or accepted palette changed.
- Backup help/exported notes now describe portable state, history and artwork,
  excluding audio, rekordbox files, credentials, native authorities and separate
  Graveyard history. Guarded legacy-art restore uses only the selected backup's
  saved source/song pins; current adoption cannot supply missing historical proof.

## Final source checks and review

The bare `./check.sh --render` passed all 5,371 discovered tests exactly once:
5,293 offscreen and 78 Cocoa cases, zero skips/expected failures. Pyflakes, engine
mypy (160 files), layout/links/instruction parity, required private fixtures and
12 Rust tests passed. All 1,000 final frozen non-Markdown inputs remain exact.
Root and an independent reviewer found no visible layout blocker in the fresh
synthetic render; this is not real-library or whole-video equivalence evidence.

Both freshly signed profiles match runtime fingerprint
`82d47c2d46aa73e05295ab78bc1a840270f014fc4411ddcc33aeaa3c3738f754`.
Artifact inspection compared 241 personal/237 public first-party runtime modules
and startup scripts to current source. Four native code/data section sets match
tested outputs and the preceding accepted installation; the complete Metal literal
and ABI9 remain. Public excludes personal code/dependencies. Both isolated Cocoa
startups passed native deck/process-query, SQL/ORM and AES checks with no library,
device or artwork worker. Their empty startup renders were byte-identical.

The review covers 151 inputs: 82 current first-party documentation/setup files
(all prose fully read; locks structurally reviewed), 28 archives reviewed for
historical scope/hazards/links and 41 upstream provenance/license inputs checked
for integrity and navigation. All 243 vendored files and 56 installed Python pins
match. The 58 missing upstream link occurrences are known omitted upstream targets;
no vendor source was rewritten. No external-link/legal recertification or new
native-source/device acceptance is claimed.

Compact per-file hashes, review depths, findings, validation and limits:
`~/Library/Logs/Soundbox/final-doc-audit-20261124/`.
Failed/incomplete gate attempts, the corrected TIFF comparison-fixture lifetime
and the existing decoder-spy's moved test target remain explicit in compact
receipts; none substitutes for the final complete passing gate.

## Installed application

`/Applications/Soundbox.app` is personal build176, native ABI9, built from the
reviewed dirty source based on `d303fef715f84a3b746430123ad0117e41c5ad39`.
Its exact runtime fingerprint is
`82d47c2d46aa73e05295ab78bc1a840270f014fc4411ddcc33aeaa3c3738f754`;
executable SHA256:
`05d20a63a0b872d29c7a42e299dfb9ee7f70efe5b24494dc1815daefe88e9316`.
This supersedes personal175/`6aa29ee1fa19bc8ecd72daeb64ac2659cf044ea0dbaf75b4248cf6880100190c`;
it is not a claim of clean-HEAD metadata at packaging time.

After Sonny confirmed normal Quit, the closed-state capture and installation
preserved all 11,376 protected canonical/record/art/metadata files exactly.
The sole temporary capture was completed after correcting a Mac metadata API
mismatch in the private helper; no app swap or state write preceded that correction.
The saved white Finder/Dock icon was preserved outside signed Contents. Normal
deep signature, exact signed Contents and current packaged-source checks pass.
Strict root-metadata rejection for that saved icon does not justify stripping it
or re-signing the running app.

Normal relaunch was at 08:08:45 UTC November24; passive verification at 08:09:09 UTC
matched loaded executable/native inode identities and all 241 runtime modules.
At verification the app loaded 3,299 tracks/86 playlists, reused 3,290 unchanged
analyses, prepared zero tasks and fetched/failed zero covers. All 11,376 protected
files and the complete logical canonical document remained exact through startup,
with no derived-cache exceptions. Saved RGB mode and the white icon preference
remained. Pre-existing missing native import/location notices were observed without
attempting a source or media repair.

## Retention and unfinished work

Sonny's November23 standing instruction: one temporary previous app during an
installation, then remove it and completed diagnostics/captures/copied DB/state/
packaging payloads once the verified update is up and running. Keep only small
result/provenance receipts. Do not recreate full archives or ask again for that
accepted-delivery cleanup. Runtime atomic publication is separate and unchanged.

The temporary previous app/state capture, both distributions, packaging
intermediates, Cargo debug output and completed render/startup/failed-gate captures
were retired after verified startup. Cleanup kept the current signed app and
active native/check/CDJ release outputs exact. At 08:09 UTC available data-volume
space measured about 24.5 GiB; APFS and other activity can change that observation.
The earlier cleanup removed 383,146 diagnostic files (10.67 GiB allocated), recorded
in `~/Library/Logs/Soundbox/diagnostic-cleanup-20261123.json`; this delivery does not
count that older reclamation again. Current delivery receipts include
`delivery-verification.json`, `delivery-cleanup.json` and `failed-gates-retirement.json`.

Preserve live canonical state and referenced records, original artwork/defaults,
original media/conversions, required offline inputs, active `.venv`, checked
native/CDJ release outputs and necessary recovery for unfinished operations.
Historical paths may now contain only compact receipts and are not replay/restore
inputs. The current curated library supersedes every dated count.

Broader MainWindow coupling, iPod planner/encoding costs, copied-media rollback
ownership, missing-link proof and separate Graveyard backup inclusion remain
tracked debt; see [critical findings](critical-audit-2026-11.md),
[performance](performance-audit.md), [roadmap](ROADMAP.md) and
[layout/retention](PROJECT_LAYOUT.md). These are separate from this release closure.

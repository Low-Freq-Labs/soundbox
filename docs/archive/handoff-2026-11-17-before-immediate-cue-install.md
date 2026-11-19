> Historical handoff checkpoint. Dated approvals grant no new app control,
> source/device writes, installation, replay, restoration or cleanup.
> Current guidance and installed identity: [HANDOFF](../HANDOFF.md) and [runtime contracts](../runtime-contracts.md).

# SoundBox current handoff

## Installed stock Sync and cue-response update — November17

Installed `/Applications/Soundbox.app`: personal172, dirty HEAD
`d6b504e07a358483a0f56880e15a412c5e414803`, source
`169a3d3e3bdb3ba6fc0cc7b4ea5f59613aef90a31a3324ada3a94802770719c9`, executable
`3510792de343025baf30e40666d394c82f2d1e9601064f98cc5a5c089d054529`.
Installed November18 03:27:40UTC; normal background launch03:28:16UTC.
Main91127/cache91130/native-read91131 were verified at that launch; read fresh
process state before using PIDs. No commit/push. Documentation changes after
this proof do not restamp the binary.

**Current hardware QA is blocked.** Sonny confirmed Favorites212 and enabled
Sync, then the real planner refused “No unambiguous existing stock playlist
association is available” before any device commit. The immutable export handoff
omits native playlist UUID rows: live association succeeds, but the worker's
materialized Library loses that identity and correctly refuses. Prior copied
proofs used the raw Library, and controller tests mocked the planner. A narrow
opt-in selected-ID/UUID/deletion-state capture plus a real capture-to-planner
regression now pass focused checks; source ahead is not installed. Keep the accepted f502
rollback rather than adding a second rollback generation. Backlog remains running.

Sonny additionally requires Hot/Memory cues to appear and work before canonical
ACK, including while playing and after switching songs. The source candidate
adds exact per-track pending overlays, ordered background admission, changed-slot
painting and retained failure/retry ownership. This explicitly supersedes the
earlier marker-after-durable-ACK UI rule, while native writes still require a
durable intention and the accepted shared writer. Focused stalled-save/switch/
failure controls pass144distinct cases. An actual Cocoa/native control holds
canonical ACK while playing: Hot/Memory creation0.953/0.916ms, two native markers,
13.685ms maximum heartbeat gap and paused→Memory preview11.010ms. The near-target
Hot check proves command/readiness, not completed seek/audibility latency.
The first corrective gate ran4,039offscreen cases with five import-analysis
failures and unchanged858inputs; Cocoa/render/package did not run. Four tests
assumed synchronous cue ACKs/faulted the superseded `_save_state` boundary. Genuine
callback probes also found marker erasure/SHA rebasing before cue admission and
a finished detector result dropped during an active canonical ACK. The candidate
now retains a joined Python-only completion capsule until admission finishes,
fences restore epochs and preserves renewed audio identity during queue compose.
The corrected focused cohort passes71cases plus the final changed-binding
rejection control: actual QThread deletion before delayed publication, exact
single result/no redetection, cancellation/owner-stop, restore/shutdown and
renewed identity controls all pass. The final bare gate now runs frozen source
`19d1a3e51d53383462ed11310d1ff10721581ebad0d2553327b84763bbf00f44`,
858inputs. That second attempt ran4,049offscreen cases: two older lightweight
hosts lacked `_state_publication` (three error subcases), and a shared USB test
left a real UI-preference ACK owning the later idle-status test. The reviewed
host initializations preserve all126assertions; accepted preference retirement
is being isolated without sleeps or a weakened Low Disk oracle. Production
`19d1a3e5…` remains unchanged; install and complete rerun remain pending.
The two host fixtures and USB preference-retirement cleanup now pass27focused
cases plus the real held-ACK cause control, preserving all original assertions.
A third bare full gate is running with those frozen test-only adjustments and
the same19d1production source. Both earlier failures remain explicit evidence.
The first failed complete offscreen attempt is retained under
`stock-sync-capture-fix-gate-20261117/failed-import-prep-compatibility/`.
The second is `failed-analysis-fixtures-status/` in the same gate.

Sonny further requires current fast local loading/full first waveform to be
preserved. Private generated180s local audio comparison: warm5load medians for
169a→19d are source-ready8.74→6.87ms and full native waveform upload14.02→14.39ms;
400paired MainWindow caller samples add~14µs. Every upload contains complete
27,000overview/180,000detail geometry; input bytes stay exact. Nine shared
dependencies, WaveformWorker and native TEXT match strictly; six load functions
have identical executable code/constants, with shifted debug lines. Fresh3
process timings vary widely and establish no cold-real-song/compositor/cloud
latency claim. The scoped warm result shows no material regression. Private
receipt: `critical-source-recovery-audit-20261117/pinned-local-load-regression-20261117/regression-receipt.json`.
Generic USBs retain explicit Export; stock/Rockbox iPods alone use Sync.

The current Favorites211/device212 difference is independently explained by
hidden local track87946313, absent from the adopted native playlist's current211
membership rows. Historical custom212 is preserved. This predates capture, with
no duplicate or lost Track binding. Without a validated witness for that extra
device entry, Sync updates matched metadata/art, preserves the extra entry/audio
and reports unchanged membership rather than guessing a deletion or new order.
The compact consistent-state/cache observation is retained privately; no new
live-source or device read/write accompanied it.

The complete bare `./check.sh --render` passed4,089tests:4,023offscreen in795.442s
and66Cocoa in71.599s, zero skips/expected failures,857unchanged frozen inputs.
Lint/layout/native/CDJ/mypy/immutable fixtures passed. All bundled first-party
code, entry/hook/startup order and25native TEXT sections match the tested source.
Signed generated cache/native helpers preserve all3,298rows, full native
metadata/duplicate/tag/cue facts and unchanged TEMP sources; both join at exit0.
The packaged isolated native deck/process/query/ORM/AES checks pass, with no
library/device discovery. Main and supported Video/Classic Sync renders were
inspected. Synthetic windows were explicitly authorized, including Sonny's
unattended testing/install permission while away; he reaffirmed after returning.

## Runtime checkpoint and resumed work

Sonny explicitly requested the backlog continue while testing. The ordinary
Resume menu advanced its earlier974checkpoint to1,167/3,121 before the brief
installation pause. The exact Pause menu drained to no active pair, pending
fields or receipt ACKs. Normal Quit initially returned AppleEvent-128, then the
app exited naturally; no force termination. The complete post-exit manifest,
referenced `.state-records/` and10,752,759-byte portable snapshot were preserved.
Signed Contents, saved white icon/resource metadata,214art/authority JSON hashes
and all curated state were verified through replacement and launch.

The strict paused comparison found only two fulfilled title-mask/shield
retirements: each matches its completed frozen target, native ID/UUID/path,
physical native title and exact own acknowledgement. No pin retired. All other
logical curation is exact, and the actual engine + Track Info +
MetadataController frozen-pair oracle preserves all3,298displayed title/artist
pairs. Never restore older progress or masks.

An initial runtime oracle expected zero prep and was retained as failed.
Startup actually completed one **cue-metadata-only cache refresh** for
track127173984: its six markers and import fingerprint exactly match the fresh
native snapshot; the record is not manually owned. No analysis, compact upgrade
or grid job was requested. The checked worker path preserves waveform payload
bytes. The revised runtime proof independently binds that one cache/header and
exact log scope, with zero artwork fetched. This was not an audio recook or a
canonical prep edit. Existing unresolved imported-file/binding diagnostics remain;
all imported curation is preserved.

After the full paused runtime proof passed, the exact Resume menu was invoked.
At03:34:02UTC the new app was active at1,170/3,121, unpaused, with no queue error;
frozen scope/order/targets and native authority remain unchanged. Progress keeps
moving; take a fresh checkpoint for any later install. Do not pause/resume or
restore current state merely to match these historical counts.

Native metadata authority remains SHA
`e707861f3db79f04a783b5fd44d80b11ab893961f3990dd37447d59fe0635655`,
profile`unicode-album-cloud-metadata-v5`, library629922790, rekordbox6.8.7.0303.
This update changes no accepted native SQL/profile. The user's existing approved
backlog continues; no new source pilot, device write, erase or restore was run.

## Delivered behavior and acceptance still needed

- Compatible writable FAT32 iPods use observed database capabilities, without
  remembered per-model/device permission. Video version25or117/checksum0 and
  Classic117/HASH58 remain distinct; Classic validates existing HASH72 and
  regenerates HASH72→HASH58 with the actual per-BSD mount's FWID. Video and
  Classic artwork descriptors/derivatives stay separate, including the observed
  Classic55-pixel/56-stride thumbnail. Unknown formats retain honest refusal.
- Existing manually transferred playlists use **Sync** for displayed title,
  artist, album, genre, comments, year, saved integer BPM and covers. A unique
  normalized name/count establishes association; stable source/UUID↔device-ID
  bindings survive later name/count/order changes. Stock incremental Sync adds
  only verified supported new files, retaining existing audio and foreign
  references. Dual catalog updates remain metadata/art only. This existing-playlist
  route can use current durable display while unrelated native backlog runs;
  ordinary new exports retain their existing source-save barriers. Generic USBs
  remain explicit selected exports, without automatic idle catalog comparisons.
- Read-only copied actual Favorites proof maps212/212songs and verifies622field
  changes on208tracks, preserving134frozen display overlays, exact unchanged
  MHIP/native layouts and both signatures. No retained-audio read/copy/delete or
  actual device commit accompanies that proof. Full-source overlap guards remain;
  eliminating redundant traversal measured1.98→0.93s on the copied plan.
- Cue-only prep uses ordered durable async admission instead of GUI whole-state/
  unrelated-grid work. Rapid gestures retain their captured positions; failures,
  source/restore changes and shutdown retain exact ownership. Isolated stress
  admission measured~0.28–0.67ms, warm durable ACK~64ms; real feel remains a user check.
- Selected cover/fallback/policy owners fence planning and commit without GUI
  image/file work. Equal-metadata image replacement invalidates an old plan;
  unrelated covers continue. Navigation stays on the current device/page during
  saves, retaining Sonny's earlier accepted device-navigation fix.
- Finalization owns dashboard/sidebar completion separately from idle inspection.
  Once-only callbacks, the original mount epoch and result owner prevent stale
  success or replacement cache/progress mutation. Queued idle Eject waits for
  actual reader retirement/join, and the OS outcome belongs only to its accepted
  session. Shutdown/new-owner/error-plus-finished controls preserve normal Eject
  and committed-with-refresh-failure truth. The combined focused cohort passes502.

Sonny's first hardware check passed: Favorites and enabled Sync are visible.
The attempted Sync then exposed the native-identity capture gap described above.
Do not retry before the correction. Do not infer successful physical metadata/art playback or cue feel from
copied/isolated tests. Walk the actual confirmation/result and cue checks one at
a time. The broader audits continue in [the ledger](../critical-audit-2026-11.md):
confirmed relink/duplicate-drop work and unconfirmed USB-draft/Rockbox safety/cache
leads are later updates, not hidden changes in this release.

## Evidence and protected retention

Private base: `~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/`.
Current `stock-sync-cue-gate-20261117/` contains full gate/frozen/package/helper/
render/isolated proofs; `runtime-handoff/` has the complete current preinstall
checkpoint, installed identity, original failed runtime oracles, the independently
proved cue-cache refresh, passed paused verification and resumed-progress receipt.
Two interrupted earlier candidates remain explicitly separate: the selected-art
race attempt had one old fixture-host error; the next passed4,004offscreen cases
but was held before Cocoa for the reproduced Eject/reconnect race. Neither is a
full release pass. Focused fixture-adjustment dispositions retain their original
behavioral oracles. Broader compact audit receipts remain in the source/recovery
and browser audit areas under `~/Library/Logs/Soundbox/`.

Keep exactly one immediate previous f502 app at this gate's
`runtime-handoff/previous/Soundbox.app` until user acceptance. The earlier35f
rollback was already retired with its exact246,363,211-byte receipt. Do not add
rollback generations. Current duplicate dist/packaging intermediates may retire
with the previous app after accepted verification; keep active native/check
builds. Preserve current AppData, imported edits, title/artist/alias/custom/hidden
curation, all16manual analyses, original music/conversions, native authority and
recovery, generation58device recovery, required immutable waveform/iPod/CDJ
fixtures and pending acceptance captures. Canonical manifests and referenced
records are inseparable; materialize portable snapshots through the repository.
Never replay completed pilots, replace references, or restore an old checkpoint.
Retaining that binary does not authorize automatic rollback. Before any later
rollback, freshly inspect pending jobs and the older reader's identity guards;
new optional native cue binding fields are not understood by the f502 reader.
Preserve the current complete canonical state and never discard pending work to
make an older app start.

Earlier delivery/progress and source review details are in
[the dated archive](../archive/handoff-2026-11-17-before-stock-sync-install.md).
Rules: [runtime contracts](../runtime-contracts.md). Device contracts:
[DEVICES](../DEVICES.md); playback/prep: [PLAYER](../PLAYER.md).

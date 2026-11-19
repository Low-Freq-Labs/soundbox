# SoundBox current handoff

## Installed and verified — November18

`/Applications/Soundbox.app`: personal172, signed dirty base
`d6b504e07a358483a0f56880e15a412c5e414803`, source
`30bb10761455a1a3822b78e76e7817ddfe654bde4ab341cc85a52f43d547f2b1`, executable
`0dc6e5c832574b95686a55b4cf2928f7ea7f53b97f02b0c6286e6c332b4a627e`.
Installed2026-11-19T03:12:01UTC; approved ordinary reopen03:12:42UTC.
Sonny quit normally and approved installation/reopen/library verification
(`call_f61d6a72b8ea4c1ea4f70156aa83b374`). Strict package verification, installed
signed Contents, deep signature and the saved white Finder/Dock icon pass.
No automatic real-app input or termination was used. Startup main47623/cache47626/
native-read47627PIDs are historical only; read fresh identity before any control.
Never restamp this artifact as a later repository commit.

Fresh preinstall state:11,641,499portable bytes and6433referenced records.
Postlaunch03:13:13UTC: complete logical state EXACT; no changed roots/settings;
all214protected authority/artwork metadata hashes match. Counts:100imports,
331title overrides,491artist overrides,10custom playlists,16analyses.
Native CoreAudio/Metal is active;3298raw tracks/85playlists,0preparation tasks,
0art fetched/failed. Metadata migration3121/3121is complete and unpaused, with no
pending native jobs. No Pause/Resume is needed for the completed backlog.
One absent bound import and two unresolved locations are preexisting state;
never restore them or intentionally deleted iPod playlists from an old checkpoint.
Accepted player behavior remains protected:18source files and20app player methods
are exact, and the signed package matches tested Python/startup/native code.

## Next session — Sonny's deferred UX requests

Sonny accepted the current speeds and requested a full wrap instead of another
runtime change. He is exporting in the running app: leave it and his controls
alone. All three delegated follow-ups have finished; none has pending edits.

1. Anchor the footer progress bar and Cancel button across Planning, Copying,
   Verifying and Finalizing. Phase text currently precedes them in
   `MainWindow._bottom_bar` (`desktop/app.py`), so changing text width can shift
   the controls despite the progress bar's fixed width. Sonny suggests placing
   messages after Cancel. Review counts/long text/narrow windows too; check both
   iPod and generic USB because they share this footer. Preserve measured progress,
   truthful finalization, real cancellation and quiet successful outcomes.
2. Review the lower-left "Sync playlist…" action after a successful Sync. The
   upper-right correctly returns to Export, while the lower-left still says Sync.
   This is currently an explicit selected-playlist action in
   `StockDeviceView._sync_playlist_button`, separate from the pending-state header;
   it is not yet evidence that the Sync failed or depends on which button was
   clicked. Resolve the wording/availability consistently without new scans or
   taking away explicit playlist reconciliation.
3. Sonny plans to tag songs and test a larger metadata Sync. The one-added-song
   reconnect/Sync passed; metadata-heavy physical acceptance remains unobserved.

Carry the existing critical audit forward rather than treating this wrap as fixing
every queued finding. The latest throughput reviews retain the preflight hash:
its early batch refusal cannot safely be removed into the known later rollback
ownership gap for an unmeasured saving. Timestamp preservation is a possible later
measurement, not a shipped speedup. Keep accepted player loading/cues untouched.

## Physical workflow acceptance — current iTunes path accepted

Sonny reconnected SONNY and requested real Export/Delete/Sync testing, including
ordinary device playlist/media deletion. Fresh OS observation03:14:47UTC verified
the same intended external writable FAT32 volume. Exact UUID/BSD/mount evidence
is private in`connected-ipod-workflow/reconnected-target.json` under the base below.
Remembered model/UUID facts grant no app write permission; current observed format,
mount, writability and signing guards remain required.

Sonny intentionally deleted device playlists himself, then completed a large
export. Root made ZERO live mutating AX calls or concurrent device/audio reads.
The last stock plan copied587new files:15,560,911,583bytes written and the same
bytes verified;533unique covers linked to587tracks. Its21:15:23–21:27:53local
commit interval was750s, about20.75MB/s including setup/artwork. Local source
capture took14.375s with no retry. Passive OS samples showed19–22MiB/s disk IO;
this suggests device IO dominates, not an independently measured hardware limit.
Stock-only export has no fixed per-song sleep. Timestamp preservation may wait
for queued writes; its sampled residence is not proof of avoidable overhead.

Completion was nonmodal. Sonny explicitly confirmed Collection AND his selected
song stayed intact; bounded AX reads separately retained TrackTable and the
footer returned to Ready. His ordinary Eject finished21:28:43local in1.106s.
SONNY was then absent from macOS. Sonny reconnected it and personally tested Sync.
Fresh03:32:19UTC OS facts verified the same UUID/writable FAT32disk4s1. He added
one song to Favorites, confirmed reconnect offered Sync, completed it, and saw
the header return to Export. His screenshot shows Favorites157, GENRES/DUBSTEP519,
LIVE SETS1 and Videos0. He reports everything feels snappy and is happy with the
speeds. These are his actual UI observations, not automated firmware inspection.
Preserve his current exports and intentional deletions. Compact metrics/code review are private under
`connected-ipod-workflow/`; no new application behavior was changed for profiling.

Sonny explicitly deferred the small gripes above to the next session and started
another export. No additional test run, app input, quit/relaunch or device write
is authorized by this documentation wrap. Broader firmware, Rockbox/dual and
large metadata Sync acceptance remain separate; no need to repeat his accepted
stock path merely to close this wrap.
Do not infer whole-collection erase, raw database replay, restoration, live fault
injection, source music changes or general control of other apps. No firmware
playback or physical throughput claim is established by generated tests.
The private AX helper was reviewed and policy-tested; every action still needs
fresh installed PID/launch/window/element and exact target guards. The separate
right-click event fallback has not been approved by root and is unnecessary.

## Delivered behavior

- Metadata/canonical work stays off the interaction path and quiet in the footer;
  native acknowledgements preserve current curation, navigation and loaded cues.
- iPod exports capture durable selected effective metadata independently of a
  paused/running bulk metadata queue. Actual selected source transitions settle;
  dormant CDJ policy does not choose iPod eligibility. Generic USB stays separate.
- Export/Sync/Apply Order proceeds from the original gesture. Successful Cancel,
  success, intentional format omissions and optional association notices are
  nonmodal. Delete asks one short question before planning. Real failures retain
  concise cause/outcome/recovery details; an export batch shows them once.
- Separate exports preserve exact surviving playlist associations, rendered
  folder labels and complete source occurrence baselines. Intentional omissions
  do not create perpetual pending additions. Explicit Sync reevaluates eligibility;
  definite unsupported WAVs use bounded reject-only headers, never admission proof.
- Managed iTunes metadata status compares seven effective fields on the worker.
  Cold witness and changed small DB/art catalogs converge through one owned native
  view read. Unchanged views update scalar status only. Clean headers offer Export;
  observed pending linked changes offer Sync; explicit playlist Sync remains.
- Completion refreshes the target's cached native view even offpage, without
  navigation. Delete renews proven surviving associations/fast baselines after
  readback, or retires only its exact unprovable old cache generation.
- Stock progress uses checked entries, actual within-file copy/readback bytes,
  artwork/database/cleanup work, and joined final readback. Only completion owns100%.
  New unindexed FAT32 media uses exclusive publication and one actual descriptor
  readback before acknowledgement/iTunesDB-last. Existing rewrites and generic
  USB keep separate contracts; this does not fix transaction-wide later rollback.

## Validation and private evidence

Base:`~/Library/Logs/Soundbox/ipod-export-checkpoint-gate-20261118/`.
Current gate/package:`connected-workflow-final/`; installation/state evidence:
`connected-workflow-final/runtime-handoff/`.
Bare`./check.sh --render` passed4648unique cases:4581offscreen674.83s plus67Cocoa
82.29s, zero skips/expected failures and all912non-document inputs unchanged.
Both fresh renders were inspected. Strict signature/full Python/startup/native
TEXT parity, generated3298-row/native SQL/AES helpers and isolated native startup
pass:Ready,0devices/no library/art worker. Actual installed startup and complete
canonical/protected-state comparison pass separately.

The first e8f candidate's full offscreen run failed two cases after4578tests:
a header fixture still expected Sync without an observed pending change, and an
unstarted wait gained unnecessary unknown-recovery text. Its complete evidence
remains at`connected-workflow-header-wait-refusal/`; no render/Cocoa ran there.
Precise strengthened header tests and a proven-pre-admission exception corrected
those paths; generic backend recovery remains explicit and deferred Cancel quiet.
The clean full run above followed. Earlier9de/14576/c9d1proofs remain in their dated
private areas; failed or phase-recovered commands are never relabeled successful.

`post-export-9de-audit/` contains independent content/oracle reviews, real generated
rollback-fault controls and final Native294/UI129/Root58focused cohorts. The final
header23/wait26corrections have separate exact receipts. `wrap-up/` reconciles all
245intended non-document files, including the inherited review and final deltas.
The earlier full142-entry documentation audit remains its basis; the final
delivery delta supplement and independent prose review are in `wrap-up/`.

## Wrap, publication and retention

Sonny requested the full wrap: validated delivery, actual workflow verification,
repository-wide documentation review, permitted cleanup, commit/push and clean
published worktree. No new publication approval is needed. He confirmed the public
`Low-Freq-Labs/soundbox` destination; current origin is that same repository,
immutable ID1312418737, and existing GitHub login has push permission. No new key
or credential export is needed. This wrap publishes the validated intended changes
on `main`; read the final repository commit with `git log -1` and compare
`origin/main`. The private `wrap-up/publication-completed.json` records the exact
final local/remote commit. Installed source/executable and its dirty d6b504e base
above remain unchanged; publication does not rebuild or restamp the running app.

After Sonny's acceptance, guarded cleanup retired1,612,310,155distinct regular-file
bytes from `dist/personal/` (both generated distributions), `build/personal/`,
`build/cdj/debug/` and the sole old f502/7178 previous-app bundle. No app input,
termination or source/device write occurred. Its reviewed manifests/receipt are
`wrap-up/connected-wrap-cleanup-{prepared,completed}.json`. There is no retained
application fallback or duplicate candidate from this delivery. Current installed
app, AppData, source originals/conversions, native/device recovery, private fixtures,
`.venv`, `build/native`, `build/check` and CDJ release outputs remain protected.
Physical space gain was not measured. Earlier2.6GB,1,242,462,326-byte and
1,242,524,360-byte cleanups retain their separate private receipts.

Current new-copy iTunes support remains MP3 or PCM16/44.1kHz stereo WAV, narrower
than Video/Classic AAC/ALAC/AIFF hardware capability. Codec-aware native fields,
classification versioning and retained dual promotion are a separate feature;
the new read-only plan is`post-export-9de-audit/later-codec-plan/`. Do not claim
all M4A hardware unsupported or complete this feature by widening suffixes.
Queued acknowledged-copy rollback/reference ownership, CDJ recovery races, USB
view state, player and artwork findings remain in[the critical audit](critical-audit-2026-11.md).
Two new generated ownership controls extend existing P1s; they are not extra bugs
or shipped fixes. Broader native metadata/analysis/deletion domains remain in
[the shared-library audit](shared-library-audit-2026-11.md).

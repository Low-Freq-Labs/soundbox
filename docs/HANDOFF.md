# SoundBox current handoff

Updated September 28, 2026, America/Chicago.

## Next session: complete the public/personal split

Sonny explicitly wants the next context session focused on finishing this split,
not another audit or proposal. Preserve the full personal build on this Mac
throughout migration and install the completed **personal** artifact at handoff.
The currently installed build 116 includes all existing personal features.

Start with [release boundaries](release-boundaries.md),
[ROADMAP section 3](ROADMAP.md#3-establish-the-shared-core--personal-module-boundary),
[GOTCHAS.md](GOTCHAS.md) and the relevant architecture/worker guidance. The
allocation and attachment contract are already agreed; implement them end to end.
Local artwork storage is provider-independent, state/restore ownership is shared,
and worker lifetime foundations exist. **The actual split is not implemented:**
desktop code still directly calls online discovery, `build_app.sh` bundles
`yt_dlp` unconditionally, and there is no optional provider registration or
public/personal build composition.

Completion requires:

1. Extract existing online cover search/fetch, SoundCloud URL cover/avatar actions
   and online Unknown-Artist suggestions behind typed optional providers and a
   versioned, explicitly selected module manifest. Core retains state publication,
   task ownership and all device authority. Preserve personal behavior.
2. Resolve availability at startup and gate menus, direct commands and background
   scheduling. Public retains local art controls, local artist merges, the same
   player/library/device UI and existing guarded exports; no dead provider panels.
3. Build both artifacts from one shared core with one packaging authority,
   separated optional dependencies and profile/module provenance. Public must
   physically exclude personal provider code/dependencies, not merely hide them.
   Initial attachment is build composition plus restart, not hot loading.
4. Test core with personal packages absent and network denied, provider failures
   and cancellation/shutdown, paired hermetic UI renders, and shared curated-state
   round trips across detach/reattach. Existing art, provenance, sticky removals,
   provider settings and analysis caches must survive without a rebuild/reset.
   Inspect both built artifacts and prove personal workflows remain available.
5. Run final validation, safely install/relaunch the full personal build, verify
   the real curated baseline, update documentation and complete the authorized
   wrap-up. Test public using isolated state with device discovery suppressed;
   do not replace Sonny's installed personal app with the public variant.

This is the next priority ahead of unrelated controller extraction or device
performance work. Generic USB optimization and CDJ export remain deferred.
New embedded-art extraction is separately planned shared functionality, not a
prerequisite for separating existing features. Physical iPod acceptance,
distribution licensing/notarization and clean-machine release gates remain
explicit; completing the split does not certify public distribution readiness.
No live device writes or firmware experiments are authorized by this handoff.

## Repository and installed app

Runtime release **`47dd54041fc2f2fae0db1911eb75c248078e6f8b`** on `main`
contains the cloud-readiness, incremental-sync, progress-spacing and all five
non-USB repeated-work fixes. Documentation-only follow-ups record installation
evidence and the next-session scope; they do not change the installed runtime
revision. The split-session scope was published in `efab936`; this follow-up
corrects repository references and stale historical/current wording only.

`/Applications/Soundbox.app` installed **22:57:11**, launched **22:57:13**:
**build 116**, clean source **`47dd540`**.

- Executable SHA-256: `76614f735453456c0aff9220c611e04fa5b6e5b93e51af30c9ccc714efaa7dd1`.
- Native dylib SHA-256: `0170f5cf2610d8da3ba76fdc0b97b45aff84dc5267c519bd4b201fd3a57c232d`.
- Runtime fingerprint, 73 source/build files: `9f02271fc1c19339b491ac34d25e0c26a5450ea9ff659e1bb4e55d124683058c`.

Stable `Soundbox Self-Signed` deep/strict verification and clean-release provenance
check passed. Independent packaged-bytecode comparison matches all 16 changed
runtime modules; the complete source fingerprint matches tested source.
Build log: `/tmp/soundbox-performance-wrap-build.log`. Candidate/installed
provenance and packaged-source proof are `performance-wrap-*.json` in the existing
rollback directory below. `/Applications` and `.git` are writable. GitHub SSH
fails public-key authentication; the existing `gh` HTTPS helper works. No global
credential/configuration changes were made.

Graceful quit independently confirmed process exit despite Apple's misleading
“User canceled” Apple-event response. Installation moved the old bundle into the
existing rollback directory and used `ditto`; AppData was never replaced.
Startup confirmed native CoreAudio/Metal, **3397 tracks / 75 playlists**,
**2891 analysis receipt reuses**, zero preparation tasks and zero artwork fetched.
Both stock-art passes reused **2767 covers**, zero preparations/failures, each
within one logged second. The UI shows **208 Favorites / 138 OG / 2882 Collection**,
Ready, playback stopped, no mounted device.
Screenshot: `/tmp/soundbox-performance-wrap-installed.png`.

## Completed scope

Sonny authorized all five non-USB findings from
[the repeated-work audit](repeated-work-audit-2026-09.md). They are implemented,
tested and installed; **generic USB/Rockbox export optimization is deferred**.

- Analysis: disposable persistent receipts bind source/cache/ANLZ identities,
  cues/BPM and policies. Minute checks reuse unchanged entries; misses share
  one manifest read. A 30-minute fallback retries silent sidecar parse failures.
  Late sidecars, source/cache changes and forced recooks still receive checks;
  player validation remains independent.
- Rows: a title edit invalidates one row; warms compute only missing rows.
  Registry/alias/policy changes still invalidate globally, generation guarded.
- Persistence: identical serialized state plus matching filesystem identity
  avoids durable publication. Player preferences coalesce for 400 ms; curation,
  backup/restore and shutdown flush immediately under existing lock/generation
  and recovery rules.
- Idle discovery: mount notifications/root timestamp changes admit workers;
  failed probes retry at 3–30 seconds. Connected drift remains responsive.
  Ordinary same-filesystem mountpoint folders receive a 30-second attachment
  grace, then stop retrying. The real old `/Volumes/Data` folder exposed this
  final correction; no volume/folder was altered.
- Stock art: persistent completion signatures skip unchanged preparation and
  pacing; missing/changed derivatives prepare normally. Selected sync retains
  pixel hashing. Canonical art is unchanged.

Inherited fixes are also included: bounded macOS CloudStorage errno-81 retries
restart captures/hashes; composite normal Sync reuses committed source/device
inventory with exact checks on affected files; explicit Verify/Deep Verify/Repair
retain their separate policies; shared export/sync progress text has room for
complete song and byte totals. See [sync audit](sync-performance-audit-2026-09.md)
and [DEVICES.md](DEVICES.md) for exact ownership/recovery boundaries.

## Validation and performance evidence

Final runtime-release gate passed at **22:53**; the documentation/reference audit
reran the same gate successfully at **23:15**:
`SOUNDBOX_IPOD_CAPTURE="$PWD/soundbox-ipod-ref/offline-lifecycle/20260926-163945" ./check.sh --render`.
Native build, pyflakes, mypy (41 modules), **995 tests / four skips**, hermetic
render and visual inspection passed. Tests use synthetic state/media or disposable
copies of offline captures; no live-source/device fault injection. Runtime-file
parity, relative Markdown links and `git diff --check` pass.

The reference audit checked all tracked Markdown files, local links and heading
anchors, repository code-path references, build/test routing and tracked-artifact
hygiene. Corrections distinguish historical firmware plans/counts from current
instructions, scope cancellation/finalization by exporter, and align WAV
derivative metadata guidance with implementation/tests. Research is linted and
its ANLZ bridge is tested, while remaining outside the app. Runtime sources and
the installed build are unchanged; no reinstall or live device operation was
needed. Historical temporary evidence paths may expire and are not repository
dependencies or permission to recreate old device experiments.

Synthetic before/after: unchanged analysis scan **4 headers + 1 DAT → zero file
opens**; ten identical state saves **10 publications / 152 fsyncs → zero**; ten
warm stock-art passes **10 manifests → zero opens**. One title edit recomputes
one row. 100 stable empty polls start zero workers; failed probes and fresh mount
folders retain bounded retry. Receipts never confer device ownership or certify
source bytes. No whole-app speed multiplier is claimed.

The development installation's initial 22:42 receipt-building pass checked 2767
covers in roughly three seconds (previous build about 37 seconds, including
27.67 seconds of intentional sleep). Later warm passes finished within one logged
second. The release installation reuses those persisted completions. Development
idle observation **22:47:56–22:49:57** recorded zero discovery workers and recurring
2891-entry analysis reuse. Final release **22:57:21–22:58:48** observation
recorded zero discovery workers; the 22:58:18 minute pass reused 2891 entries
with zero preparation. Evidence is recorded in
`performance-wrap-installed-launch.log` / `performance-wrap-runtime-observation.json`.

Prior user-initiated generation 63 remains the real one-title Sync acceptance:
**18.30 s plan + 8.46 s commit = 26.76 s**, **69,562,392 canonical bytes read**,
**34,781,296 device bytes written**; 2880 retained sources/media and 14,405 artwork
files, zero source-proof rehashes. Generation 62 took 1942.83 seconds with two
edits; the roughly 73× comparison is not matched workload. Logs are
`generation62-controlled-sync.log` / `generation63-incremental-sync.log` in rollback.
No agent-initiated physical Sync or firmware changes occurred.

## Curated state and rollback

Final post-quit/postlaunch `library.json` SHA-256:
`f5acb14c3c9b34170143ea33cbdf9e612b3a69dc7201d081a3a5c351d6dedc30`.
**208 Favorites / 138 OG / 2882 Collection**, **3298 art files**, **2923 analysis
records** remain intact. Only the new disposable analysis and stock-art
`preparation-index.json` files were added; existing art/analysis/stock derivative
files remain unchanged. Release pre-quit, post-quit and postlaunch snapshots are
byte-identical; no art or analysis cache differences. State transaction/retirement
journals are absent. Release evidence is `library-performance-wrap-*.json` and
`performance-wrap-*-state.json` in rollback.

The first quit in this task changed only Qt `settings.columns_state` from the
pre-task SHA `22a121d742609713ab7fe646de00caacff23fd5c35ec399c0f6372eb7f1c8ed5`.
Isolated Qt decoding found first-column width **541→531**; other widths,
visibility/order and all curation were unchanged. Detailed before/closed/final
snapshots and decoded column evidence are `library-repeated-work-*.json` and
`repeated-work-*-state.json` / `repeated-work-column-state.json` in rollback.

Preserve Sonny's prior title edit `134230118` and hidden track `258721628`
(September 28 22:05). Never restore older 202-Favorite/2883-Collection snapshots.
Use the existing rollback directory only:
`/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`.
`before-performance-wrap.app` is the verified 22:47 development build 115.
`before-repeated-work.app` is the previous incremental/spacing build;
`before-repeated-work-final.app` is the intermediate 22:42 build. Intermediate
fingerprints/logs are explicitly `repeated-work-initial-*`. The earlier handoff
is `handoff-before-repeated-work.md`; prior rollback evidence and
`/private/tmp/soundbox-sync-performance-rollback.vlodpruy/` remain preserved.

Preserve generation-58 recovery **`20260926-152340-47477cfa4a49`** until separately
reviewed physical acceptance. Recent committed recovery: generation 62 stock
`20260928-215713-8a36e6ed89fd`, dual `20260928-215712-0762939ebf20`; generation 63
stock `20260928-220312-efb49787df72`, dual `20260928-220312-9df8776de224`.
Pristine offline capture: `soundbox-ipod-ref/offline-lifecycle/20260926-163945/`;
never restore it to hardware without a reviewed plan. Last observed iPod UUID
`F83BEF4A-47F1-31F7-96EA-E2BA53049BE1`, `/Volumes/IPOD`, `disk4s2`; revalidate on
reconnect. Source audio, rekordbox and `/PIONEER` remain read-only/untouched.

## Remaining boundaries

These five audited non-USB fixes are complete. Generic USB export comes later.
[ROADMAP.md](ROADMAP.md) still separately tracks A5 queue integration, A7/A9/A10
worker/performance probes, provider packaging and controller extraction. Physical
gates remain: no-op/playlist-only Sync, overlapping drops, cancel/retry, normal
eject and repeated Random/reconnect. No new physical reconnect/Sync was initiated
for this background-work change. The prior OOM root cause remains unestablished;
no firmware experiments are authorized. Keep generation-58 recovery intact.

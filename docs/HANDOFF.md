# SoundBox current handoff

## Release closure — November23, 2026

Branch `main`. This delivery closes the reviewed transport/artwork fixes and the
additional legacy-art restore ownership guard. The installed runtime matches the
checked source fingerprint below. Exact release-commit/remote/clean-worktree proof
is recorded in `~/Library/Logs/Soundbox/wrapup-20261123-final/publication.json`.
Signed app provenance retains its true build-time base; publication does not
restamp or rebuild an already verified binary merely to change its Git label.

## Delivery behavior

- Requested Play survives transient paused/refill observations. Explicit Pause
  cancels queued starts; retry preserves the latest seek/cue target, complete
  waveform and cues. Reentrant poll continuations cannot publish onto a newer
  source or transport command.
- Browser navigation binds exact source/song/visible-occurrence cursors, advances
  rapid requests through held fades and commits only accepted loads. Artists
  Load/Info uses its selected visible leaf; loaded Info fallback requires the
  committed source/song/path. Newer selections and drafts survive completion.
- Per-song artwork, misses, blocks, defaults and review/undo bind source/song
  identity. Compatible refresh carries unchanged selected choices; source/ABA
  transitions retire old publishers. Manual preparation reserves its choice
  against older automatic results. Info receives complete decoded artwork.
- Legacy-art restoration requires the selected backup's saved source/song pins.
  A current adoption manifest cannot supply missing historical identity proof.
  Refusal preserves all current state/art; valid UUID/stable-import restore stays.
- Mini-cue readiness, authored regular-playlist occurrence order, accepted RGB/BLUE
  geometry and independent 3BAND/PWV6 behavior remain. This delivery changes no
  native waveform code/shader or accepted palette.

## Final source checks and review

The bare `./check.sh --render` passed all 5,366 discovered tests exactly once:
5,288 offscreen and 78 Cocoa cases, zero skips/expected failures. Pyflakes, engine
mypy (160 files), layout/links/instruction parity, required private fixtures and
12 Rust tests passed. All 998 frozen non-Markdown inputs remain exact after the
backup fix; the added preservation regression ran in the complete gate. An
independent reviewer found no visible layout issue in the fresh synthetic render.

Both freshly signed package profiles match runtime fingerprint
`6aa29ee1fa19bc8ecd72daeb64ac2659cf044ea0dbaf75b4248cf6880100190c`.
Artifact inspection compared 240 personal/236 public first-party runtime modules
and startup scripts to current source; four native code/data section sets match
tested outputs. Public excludes personal code/dependencies. Both isolated Cocoa
startups passed native deck/process-query, SQL/ORM and AES checks with no library,
device or artwork worker. Their empty startup renders are byte-identical.

The review accounts for 148 inputs: 79 current first-party documentation/setup
files (all prose fully read; locks structurally reviewed), 28 archives reviewed for
historical scope/hazards/links, 38 upstream inputs checked for provenance/integrity/
links, and 3 additional linked license/provenance inputs. All 243 vendored files
and 56 installed Python pins match. The 58 missing upstream link occurrences are
known omitted upstream targets; no vendor source was rewritten.

Compact per-file hashes, review bases/depths, findings and limitations:
`~/Library/Logs/Soundbox/wrapup-20261123-final/`.
No external-link/legal recertification, new native-source/device acceptance,
whole-video equivalence or exhaustive absence of QOL bugs is claimed.

## Installed application

`/Applications/Soundbox.app` is personal build175, native ABI9, built from the
reviewed dirty source based on `dd2736870002917f3b635beab24191f30ffeb6be`.
Its exact runtime fingerprint is
`6aa29ee1fa19bc8ecd72daeb64ac2659cf044ea0dbaf75b4248cf6880100190c`;
executable SHA256:
`cbd3c0c214c64028fc54f77da30b6dbbb229607d45aeac552a895893744b161a`.
This supersedes the earlier build175 fingerprint, includes the final restore
guard, and is not a claim of clean-HEAD metadata at packaging time.

After Sonny confirmed normal Quit, installation verified the package, preserved
one temporary previous app/state copy, and compared exact signed Contents and all
11,376 protected canonical/record/art/metadata files before and after the swap.
The saved white Finder/Dock icon was preserved outside signed Contents. Normal
deep signature and current packaged-source checks pass; strict root metadata
rejection is expected for the saved custom icon and does not justify stripping
it or re-signing the running app.

Normal relaunch was at 05:44:36 UTC November24; passive verification at 05:45:32 UTC
matched loaded executable/native inode identities and all 240 runtime modules.
The app loaded 3,299 tracks/86 playlists, reused 3,290 unchanged analyses, prepared
zero tasks and fetched/failed zero covers. All 11,376 protected files and the
complete logical canonical document remained byte/value exact through startup.
The pre-existing missing native import/location notices were observed without
attempting a source or media repair.

The temporary previous app and state capture, both distribution apps, packaging
intermediates, Cargo debug output and completed render/startup captures were
retired after that verified running handoff. Only small result/provenance receipts
remain for the delivery. Cleanup checked inactivity and current app identity;
its installed executable stayed exact. Current free data-volume space is about
24.8 GiB (displayed 25 GiB); live state, originals, recovery, fixtures and Seafile
were outside deletion scope. Receipts: `install.json`, `runtime-verification.json`
and `accepted-delivery-cleanup.json` in the final wrap-up log directory.

## Retention and unfinished work

Sonny's November23 standing instruction: one temporary previous app during an
installation, then remove it and completed diagnostics/captures/copied DB/state/
packaging payloads once the verified update is up and running. Keep only small
result/provenance receipts. Do not recreate full archives or ask again for that
accepted-delivery cleanup. Runtime atomic publication is separate from archived
diagnostics and remains unchanged.

The storage cleanup removed 383,146 diagnostic files (10.67 GiB allocated), leaving
about 14 MiB of compact receipts/active logs. Available space measured 13.9→24.8 GiB;
APFS and other activity can affect the observation. Required fixtures passed
before/after; current app/media/Seafile were untouched. Receipt:
`~/Library/Logs/Soundbox/diagnostic-cleanup-20261123.json`.

Preserve live canonical state and referenced records, original artwork/defaults,
original media/conversions, required offline inputs, active `.venv`, checked
native/CDJ release outputs and necessary recovery for unfinished operations.
The earlier original/post-adoption diagnostic snapshots and completed captures
are retired; historical paths may now contain only compact receipts and are not
replay/restore inputs. The current curated library supersedes every dated count.

Broader MainWindow coupling, iPod planner/encoding costs, copied-media rollback
ownership, missing-link proof and separate Graveyard backup inclusion remain
tracked debt; see [critical findings](critical-audit-2026-11.md),
[performance](performance-audit.md), [roadmap](ROADMAP.md) and
[layout/retention](PROJECT_LAYOUT.md). These are separate from this release closure.

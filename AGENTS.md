# SoundBox — start here

Native macOS library/prep/player app bridging rekordbox to USB, stock iPod and Rockbox.

## Read before work

1. Read [docs/HANDOFF.md](docs/HANDOFF.md) for current source/install state and protected retention.
2. Read [docs/runtime-contracts.md](docs/runtime-contracts.md) in full for the source/state/lifecycle rules.
3. Read the relevant folder README; [docs/README.md](docs/README.md) routes deeper references.
4. Read [GOTCHAS](docs/GOTCHAS.md) before worker/device/cache changes and [PLAYER](docs/PLAYER.md) before playback/native changes.

## Working rules

- Preserve QOL: ordinary desktop pages/services prepare before interaction; USB/iPod pages/backends initialize on demand.
- Track-dependent preparation follows selection/load; complete selected artwork precedes Track Info's first frame.
- Speed work preserves complete first frames, coordinated audio/waveform readiness, selection/drafts and durable saves; earlier partial readiness or pop-in is a QOL regression.
- Keep the engine Qt-free, production independent of research/tests, and personal code behind provider admission.
- Reuse cohesive internal modules; keep authority-specific source SQL, recovery and exporter rules separate.
- Target Sonny's one current installation. Retire obsolete compatibility after checking or migrating his actual saved state, backups, devices and recovery; do not keep speculative support for other installations.
- Native source edits require their own accepted exact-library authority, fresh guards and shared writer/lock/journal.
- Supported library/prep edits must persist in the shared rekordbox DB. SoundBox's current effective titles, artists and organization are authoritative migration inputs; preserve them before any native refresh or write. Local-only success is unfinished work.
- Playback/export preserves source audio; only separately accepted conversion and user-confirmed removal may change originals.
- iPod backups belong to the user. Never make whole-device/media-tree backups or retain completed catalog, artwork or media rollback payloads. Keep only minimal in-flight transaction recovery and compact completion receipts; ordinary Sync/Delete work stays scoped to affected managed contents.
- Generic USBs export selected playlists explicitly; no automatic sync status or idle catalog comparisons.
- Preserve curated AppData, current user edits, original conversions, recovery, immutable fixtures and pending-acceptance rollbacks.
- Keep one temporary previous app only during installation. Once the updated app is verified and Sonny is up and running, remove that rollback plus completed diagnostics, captures, copied DBs/state and packaging scratch as part of finishing the update. Keep only small result receipts; do not accumulate full archives. This standing cleanup authorization supersedes older diagnostic/state-snapshot retention. Preserve live curation, original audio/conversions, required offline fixtures and recovery for unfinished operations; it does not require disabling atomic saves.
- Canonical `library.json` and referenced `.state-records/` are inseparable; materialize portable snapshots through the state repository.
- No automated input/termination of real apps, live-source tests, device erase/replay, restoration or permanent deletion without current explicit scope.
- Work autonomously on reversible local changes. Inspect and preserve the dirty worktree.
- Keep parallel critical-path audits running alongside focused fixes; record reproducible findings separately and deliver validated fixes in later updates without blocking the active release on unrelated audits.
- Do not commit/push unless asked; “wrap up” authorizes validated completion/publication under the detailed contracts.

## Checks and delivery

Use Python 3.14 and `.venv`; pinned dependencies are in `requirements.txt`.

```sh
./check.sh --list-suites  # named areas and cumulative quick/integration/recovery levels
./check.sh --suite tags playlists --level integration  # combine affected areas
./check.sh --focus tests/test_sidebar_layout.py  # exact module/class/method
./check.sh --render       # complete gate before commit/push or wrap-up; run bare
./build_app.sh            # personal package; public uses ./build_app.sh public
```

Choose a named area and depth with `--suite AREA... --level quick|integration|recovery`;
preview exact coverage/platforms with `--plan`, or use `--focus` for exact selectors.
USB/CDJ and iPod have separate core suites. Native writer/SQL/journal/ACK changes
need the affected recovery coverage; quick reader/UI smoke is not writer proof.
Small local fixes use focused regressions plus affected integration/lifecycle/error
paths, relevant lint/types, and Cocoa/render checks when appearance or macOS input
requires them. Reuse existing meaningful tests; do not run the whole suite for
each small correction or repeat passing focused checks without changed inputs or
an unresolved concern. Broaden coverage when the changed dependencies warrant it.
Local installs may use this focused evidence plus package/isolated/runtime checks.
Before any commit/push or wrap-up, run the complete gate on the intended final tree.
In the full gate every discovered test runs exactly once; focused checks run every
selected test once. Neither allows skips, weakened oracles or substituted fixtures.
Record focused coverage honestly; it is not a full-suite pass.
Build/isolated checks do not grant real-app control or source/device writes.
A runtime handoff requires the guarded install/relaunch procedure and its current authorization.
Record source validation separately from installed app identity; never restamp an older binary as new.
Keep only one temporary previous app during installation; retire it after accepted verification. Do not accumulate app rollback bundles.

## Documentation

Keep notes durable: explain responsibilities, entry points, relationships and important invariants. Code comments explain why or who owns work; dated changes belong in history and installed status belongs in HANDOFF.
Keep folder READMEs within 25 lines: purpose, entry points, key rules, checks and links. Update guides with structural changes and keep every maintained directory reachable from the root README.
Update behavior docs with code; keep history in dated records, not current instructions.
Keep `AGENTS.md` and `CLAUDE.md` byte-identical whenever shared instructions change.
Private music-acquisition workflows stay outside this repository.

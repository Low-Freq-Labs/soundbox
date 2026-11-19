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
- Keep the engine Qt-free, production independent of research/tests, and personal code behind provider admission.
- Reuse cohesive internal modules; keep authority-specific source SQL, recovery and exporter rules separate.
- Native source edits require their own accepted exact-library authority, fresh guards and shared writer/lock/journal.
- Supported library/prep edits must persist in the shared rekordbox DB. SoundBox's current effective titles, artists and organization are authoritative migration inputs; preserve them before any native refresh or write. Local-only success is unfinished work.
- Playback/export preserves source audio; only separately accepted conversion and user-confirmed removal may change originals.
- Generic USBs export selected playlists explicitly; no automatic sync status or idle catalog comparisons.
- Preserve curated AppData, current user edits, original conversions, recovery, immutable fixtures and pending-acceptance rollbacks.
- Keep diagnostic captures bounded: compact receipts by default; retain only needed current full proof/recovery payloads and retire completed duplicates.
- Canonical `library.json` and referenced `.state-records/` are inseparable; materialize portable snapshots through the state repository.
- No automated input/termination of real apps, live-source tests, device erase/replay, restoration or permanent deletion without current explicit scope.
- Work autonomously on reversible local changes. Inspect and preserve the dirty worktree.
- Keep parallel critical-path audits running alongside focused fixes; record reproducible findings separately and deliver validated fixes in later updates without blocking the active release on unrelated audits.
- Do not commit/push unless asked; “wrap up” authorizes validated completion/publication under the detailed contracts.

## Checks and delivery

Use Python 3.14 and `.venv`; pinned dependencies are in `requirements.txt`.

```sh
./check.sh --render       # complete lint/types/build/offscreen/Cocoa gate; run bare
./build_app.sh            # personal package; public uses ./build_app.sh public
```

Every discovered test runs exactly once; no skips, weakened oracles or substituted fixtures.
Build/isolated checks do not grant real-app control or source/device writes.
A runtime handoff requires the guarded install/relaunch procedure and its current authorization.
Record source validation separately from installed app identity; never restamp an older binary as new.
Keep only one temporary previous app during installation; retire it after accepted verification. Do not accumulate app rollback bundles.

## Documentation

Keep folder READMEs within 25 lines: purpose, entry points, key rules, checks and links.
Update behavior docs with code; keep history in dated records, not current instructions.
Keep `AGENTS.md` and `CLAUDE.md` byte-identical whenever shared instructions change.
Private music-acquisition workflows stay outside this repository.

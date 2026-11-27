# Documentation

- [Handoff](HANDOFF.md): current source/install state; [next session](NEXT_SESSION.md): small autonomous work sections.
- [Getting started](getting-started.md): prerequisites, source launch and build commands.
- [Architecture](ARCHITECTURE.md) · [Player](PLAYER.md) · [Devices](DEVICES.md)
- [Runtime contracts](runtime-contracts.md): detailed source/state/lifecycle safety rules.
- [Gotchas](GOTCHAS.md): implementation traps; [Keyboard](KEYBOARD.md): action/hold semantics.
- [Release gate](test-release-gate.md) · [Public/personal boundaries](release-boundaries.md)
- [First launch](first-launch.md): library selection, cleanup/artwork choices and curation rules.
- [macOS installer](macos-installer.md): app-only beta installation, upgrades and signing scope.
- [Personal beta RC checklist](rc-beta-checklist.md): full-feature shakedown plan, RB6/7 matrix and clean-user acceptance.
- [RC execution](rc-beta-results-2026-11-26.md): dated results, confirmed blockers and action/write inventories.
- [Physical USB execution](usb-physical-results-2026-11-26.md): authorized TEST-2 evidence and separate manual/firmware boundaries.
- [RC write ledger](rc-write-ledger.md): partial native/device ownership map and current GAP-001/002 reproductions.
- [Roadmap](ROADMAP.md): committed sequence; [Ideas](IDEAS.md): uncommitted UX proposals.
- [Project layout](PROJECT_LAYOUT.md): fixtures, generated output and protected retention.
- [Performance evidence](performance-audit.md): dated measurements and remaining costs.
- [Shared-library audit](shared-library-audit-2026-11.md): current feature coverage, curation preservation and native gaps.
- [Native prep](rekordbox-prep.md) · [Playlist ordering](native-playlist-order.md): accepted source domains, exact occurrences and durable pending edits.
- [Critical-path audits](critical-audit-2026-11.md): ongoing delegated findings, isolated proof and staged fixes.
- [Archive](archive/README.md): historical evidence, never new action authority.

Keep folder READMEs within 25 lines; link to a focused reference rather than copying it.
Separate current behavior, proposed work and dated evidence. Never copy old approvals into new work.
Run `PYTHONPATH=. ./.venv/bin/python scripts/check_layout.py` from the root after documentation changes.

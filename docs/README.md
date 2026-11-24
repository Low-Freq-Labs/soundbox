# Documentation

- [Handoff](HANDOFF.md): current source/install identity, validation and unfinished work.
- [Getting started](getting-started.md): prerequisites, source launch and build commands.
- [Architecture](ARCHITECTURE.md) · [Player](PLAYER.md) · [Devices](DEVICES.md)
- [Runtime contracts](runtime-contracts.md): detailed source/state/lifecycle safety rules.
- [Gotchas](GOTCHAS.md): implementation traps; [Keyboard](KEYBOARD.md): action/hold semantics.
- [Release gate](test-release-gate.md) · [Public/personal boundaries](release-boundaries.md)
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

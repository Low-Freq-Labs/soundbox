# SoundBox

Native macOS music-library app: rekordbox → USB, stock iPod and Rockbox, with a CoreAudio/Metal player.

- [Start and build](docs/getting-started.md) · [Current handoff](docs/HANDOFF.md)
- [Developer instructions](AGENTS.md) · [Architecture](docs/ARCHITECTURE.md) · [Layout/retention](docs/PROJECT_LAYOUT.md)
- [Desktop](desktop/README.md): pages, browser, player and task ownership.
- [Engine](soundbox/README.md): library, source transactions, analysis and exporters.
- [Native](native/README.md): audio/Metal deck, process guard and CDJ renderer.
- [Profiles](profiles/README.md) · [Personal providers](soundbox_personal/README.md): package membership and optional services.
- [Assets](assets/README.md) · [Research](research/README.md): bundled visuals and separate offline proof tools.
- [Tests](tests/README.md) · [Tooling](scripts/README.md) · [Documentation](docs/README.md)

Each maintained folder's README maps its purpose, entry points and boundaries; update it with structural changes.

Ordinary desktop controls prepare before interaction; attached-device features initialize when needed.
Playback/export preserves source audio. Native edits, conversion and deletion retain separate guarded authorities.
Curated AppData, recovery and private reference fixtures are protected; never clear them for cleanup.

Use Python 3.14, Apple Silicon/macOS 14+, the pinned requirements and the local `.venv`.
Choose areas/depth with `./check.sh --list-suites` and `--suite AREA... --level LEVEL`.
Exact small fixes: `./check.sh --focus tests/test_name.py` plus relevant static/integration checks.
Before commit/push or wrap-up: `./check.sh --render`; build with `./build_app.sh`.
Building does not authorize installation, real-app input or source/device writes.

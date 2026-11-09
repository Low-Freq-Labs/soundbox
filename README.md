# SoundBox

Native macOS music-library app: rekordbox → USB, stock iPod and Rockbox, with a CoreAudio/Metal player.

- [Start and build](docs/getting-started.md) · [Current handoff](docs/HANDOFF.md)
- [Developer instructions](AGENTS.md) · [Architecture](docs/ARCHITECTURE.md)
- [Desktop](desktop/README.md): pages, browser, player and task ownership.
- [Engine](soundbox/README.md): library, source transactions, analysis and exporters.
- [Native](native/README.md): audio/Metal deck, process guard and CDJ renderer.
- [Tests](tests/README.md) · [Tooling](scripts/README.md) · [Documentation](docs/README.md)

Ordinary desktop controls prepare before interaction; attached-device features initialize when needed.
Playback/export preserves source audio. Native edits, conversion and deletion retain separate guarded authorities.
Curated AppData, recovery and private reference fixtures are protected; never clear them for cleanup.

Use Python 3.14, Apple Silicon/macOS 14+, the pinned requirements and the local `.venv`.
Run `./check.sh --render` directly for the complete release gate; build with `./build_app.sh`.
Building does not authorize installation, real-app input or source/device writes.

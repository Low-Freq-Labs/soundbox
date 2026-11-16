# Desktop

PySide6 presentation and application orchestration; the engine stays Qt-free.

- Start at `app.py` for composition, library publication and signal wiring.
- `model.py` / `trees.py`: browser rows, hierarchy and drag/drop behavior.
- `artists_browser.py`: prepared artist groups and incremental tree presentation.
- `player.py` / `native_deck.py`: Qt controls and the native playback boundary.
- `tag_creation.py` / `tag_creation_controller.py`: tag forms and durable, independently admitted definition changes.
- Domain controllers own prep, imports, playlists, artwork and device interaction.
- `metadata_controller.py`: durable paired edits, policy/merge generations and sparse refresh; exact native readback precedes acknowledgement.
- `task_owner.py` owns workers through finished delivery and join; use it for new tasks.
- `keyboard.py` and `theme.py` define shared input actions and appearance roles.
- Export workers consume immutable captures; My Tags dismisses before outside browser input proceeds.

Prepare ordinary pages/services before interaction; device pages/backends stay on demand.
Keep selected artwork ready before Track Info appears. Preserve selection, scroll and drafts on refresh.

Check with `./check.sh --render` from the root; tests must isolate AppData and suppress discovery.
Read [gotchas](../docs/GOTCHAS.md), [player](../docs/PLAYER.md) and [keyboard](../docs/KEYBOARD.md) before changes.

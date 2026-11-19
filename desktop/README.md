# Desktop

PySide6 presentation and application orchestration; the engine stays Qt-free.

- Start at `app.py` for composition, library publication and signal wiring.
- `model.py` / `trees.py`: browser rows, hierarchy and drag/drop behavior.
- `artists_browser.py`: prepared artist groups and incremental tree presentation.
- `player.py` / `native_deck.py`: Qt controls and the native playback boundary.
- `tag_creation.py` / `tag_creation_controller.py`: tag forms and durable, independently admitted definition changes.
- Controllers own prep/imports/playlists/devices; `stock_catalog_sync.py` owns catalog Sync and `device_progress.py` maps measured phases.
- `metadata_controller.py`: durable paired edits, policy/merge generations and sparse refresh; exact native readback precedes acknowledgement.
- `native_publication.py`: metadata snapshot compatibility and prepared import reuse; structural changes retain full loading.
- `prepared_browser.py`: detached visibility, organization and registry inputs for sparse Collection/Artists acknowledgements.
- `state_commit.py`: ordered metadata deltas and exact UI preference leaves; enqueue admits work, durable callbacks complete it.
- `inline_drafts.py`: exact song/field-bound unsaved editor text through refresh, with explicit recovery for changed targets.
- `task_owner.py` owns workers through finished delivery and join; use it for new tasks.
- `keyboard.py` and `theme.py` define shared input actions and appearance roles.
- Export workers consume immutable captures; My Tags dismisses before outside browser input proceeds.
- `ipod_export_capture.py` / `ipod_export_fences.py`: saved selected generations and physical transitions, independent of bulk metadata progress.

Prepare ordinary pages/services before interaction; device pages/backends stay on demand.
Keep selected artwork ready before Track Info appears. Preserve selection, scroll and drafts on refresh.

Check with `./check.sh --render` from the root; tests must isolate AppData and suppress discovery.
Read [gotchas](../docs/GOTCHAS.md), [player](../docs/PLAYER.md) and [keyboard](../docs/KEYBOARD.md) before changes.

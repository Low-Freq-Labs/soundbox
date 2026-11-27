# Desktop

PySide6 presentation and application orchestration; the engine stays Qt-free.

- Start at `app.py` for composition; `onboarding.py` saves first-launch choices before library/services start.
- `model.py` / `trees.py`: browser rows and drag/drop; `rekordbox_playlists.py` owns durable membership gestures and occurrence identity.
- `artists_browser.py`: prepared artist groups; `browser_columns.py`: keyed shared defaults and source/playlist-bound header layouts.
- `player.py` / `native_deck.py`: Qt controls and native playback; `playback_navigation.py` pins source/song occurrences and accepted load ownership.
- `tag_creation.py` / `tag_creation_controller.py`: tag forms; `source_intent_admission.py` stages small source queues until durable ACK.
- Controllers own prep/imports/playlists/devices; `usb_setup.py` offers empty CDJ setup, `usb_settings.py` admits durable quiet preference changes, `stock_catalog_sync.py` owns catalog Sync, and `device_progress.py` maps measured phases.
- `metadata_controller.py` / `metadata_batch.py`: durable paired edits, policy/merge generations and sparse refresh; bounded existing writes share a complete fresh readback before their durable acknowledgements.
- `native_publication.py`: metadata snapshot compatibility and prepared import reuse; structural changes retain full loading.
- `prepared_browser.py`: detached visibility, organization and registry inputs for sparse Collection/Artists acknowledgements.
- `state_commit.py` / `quit_saves.py`: ordered deltas, UI preferences and verified failed-queue retention; admission is not durability.
- `inline_drafts.py`: exact song/field-bound drafts, outside-click completion, editable-cell Tab navigation and explicit recovery for changed targets.
- `task_owner.py` owns workers through finished/join; `artwork_requests.py` prepares selected images before guarded GUI publication; `image_decode.py` keeps Qt decoder I/O native.
- `keyboard.py` and `theme.py` define shared input actions and appearance roles.
- Export workers consume immutable captures; `device_playlists.py` shares the stock/USB frame; `usb_device_view.py` hydrates preference tabs on demand, and `stock_device_view.py` reuses complete unchanged captures. My Tags dismisses before outside browser input proceeds.
- `ipod_export_capture.py` / `ipod_export_fences.py`: saved selected generations and physical transitions, independent of bulk metadata progress.

Prepare ordinary pages/services before interaction; device pages/backends stay on demand.
Keep selected artwork ready before Track Info appears. Preserve selection, scroll and drafts on refresh.

Use affected `./check.sh --suite AREA... --level LEVEL` checks locally; full `--render` before publication.
Read [gotchas](../docs/GOTCHAS.md), [player](../docs/PLAYER.md) and [keyboard](../docs/KEYBOARD.md) before changes.

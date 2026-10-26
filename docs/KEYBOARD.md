# Keyboard settings

**Settings → Keyboard** is a searchable two-column list of action groups. Each
action's keycaps, removal controls, Add shortcut, and per-action Reset stay together
on the same row. Multiple bindings wrap within the row. Click a keycap to replace
it, or Add shortcut to record another key/modifier combination. A conflicting
assignment names its current action and offers an explicit Move shortcut; it
never silently steals another action's binding. Reset all asks before replacing
the user's configured mappings with defaults.

The existing defaults remain Space for play/pause, C for held session CUE, and
Command-R for Rename selected item (including the artist-pictures catalog).
Additional cue defaults:

| Key | Action |
| --- | --- |
| 1–8 | Preview A–H on Hot Cue, or Memory Cues 1–8 on Memory Cue |
| 9 | Select the Hot Cue page |
| 0 | Select the Memory Cue page |
| = | Zoom in |
| - | Zoom out |

Cue keys use press/release, including latching through the configured Play action.
Empty slots do nothing. A held active-page cue remembers its original page and
slot, so switching pages or releasing modifiers first cannot strand or release
the wrong cue. Focus loss, shutdown and a successfully changed keymap release
held keyboard controls. Repeated key events never retrigger commands. Multiple
bindings sharing the same control keep it held until the final key releases.

The cue controls use the active page only; the separate Direct Hot Cues and Direct
Memory Cues sections have been removed. Any older saved direct-cue bindings remain
preserved as inactive extension data and cannot trigger a hidden shortcut.
The catalog also exposes unassigned actions for track navigation, beat jumps,
waveform zoom/display, autoplay,
loading/renaming, search/now-playing, library refresh/import/new playlist, and
Settings. Available actions share one catalog in `desktop/keyboard.py`; adding an
action requires a real dispatcher and tests, not only an editor row.

Bindings operate inside SoundBox from the normal browsing surfaces. Search,
inline editors, ordinary dialogs, settings lists and device filesystem browsing
retain normal typing/navigation. Open/close Settings also works from nontext
settings controls. These are application mappings, not global OS hotkeys. Native
text editing and standard widget navigation remain platform behavior. This first
version supports modifier combinations and multiple bindings per action, not
multi-stroke sequences, macros, MIDI or additional decks.

`settings.keyboard` is a versioned schema-1 action→bindings overlay in the existing
portable library state. Unassigned overrides are explicit empty lists; missing
actions inherit defaults. Unknown future action IDs/fields survive edits and
reset. A future unsupported schema remains preserved and read-only. Corrupt
ambiguous bindings do not execute. Failed saves retain the previous live map.
Restore reloads both the editor and the router; no separate keyboard file can
silently override restored user state.

Tests cover conflict moves, persistence/unbinding/reset, modifier/focus changes,
autorepeat, multiple holds, page switching, failed-save rollback and text-focus
boundaries. Source changes require the full release/render gate and installed
native-player acceptance; see [HANDOFF](HANDOFF.md) for the current state and
[PLAYER](PLAYER.md) for the read-only cue/transport contract.

# Keyboard settings

**Settings → Keyboard** is a searchable two-column list of action groups. Each
action's keycaps, removal controls, Add shortcut, and per-action Reset stay together
on the same row. Multiple bindings wrap within the row. Click a keycap to replace
it, or Add shortcut to record another key/modifier combination. When editing an
existing keycap, **Unbind** sits immediately left of Save and removes that shortcut
through the normal save path. Other shortcuts for the action remain. Removing its
last binding persists an explicit unassigned override; a failed save restores the
previous mapping. Unbind is inactive when adding a new shortcut. A conflicting
assignment names its current action and offers an explicit Move shortcut; it
never silently steals another action's binding. Reset all asks before replacing
the user's configured mappings with defaults.

Newly created cues appear and can be previewed immediately while database saves
run in background. The creation press/release itself stays inert for preview.
The existing defaults remain Space for play/pause, C for held session CUE, and
Command-R for Rename selected item (including the artist-pictures catalog).
Additional cue defaults:

| Key | Action |
| --- | --- |
| 1–8 | Trigger a populated active-page cue; set an empty cue at the playhead, or nearest beat with Q on |
| Command-1–8 | Delete the corresponding active-page cue |
| 9 | Select the Hot Cue page |
| 0 | Select the Memory Cue page |
| = | Zoom in |
| - | Zoom out |

**Toggle cue page** switches between Hot Cues and Memory Cues from the same
Cue pages group. It starts unassigned; use Add shortcut to choose its key. The
separate 9/0 page-selection defaults remain available. Switching pages preserves
any held cue's original page/slot until release.

Cue keys use press/release, including latching through the configured Play action.
On an accepted library with rekordbox/agent closed, empty slots create without
starting preview. Q off keeps the exact captured playhead; Q on snaps new cues to
the nearest current beat. Existing cues always trigger their exact saved positions. Hot cues keep ordinary
playback running; while paused they retain hold-preview and Space-latch behavior.
The Q button is global and persistent. **Beatgrid** provides bindable actions
for Set bar start, Shift left/right, Double/Halve BPM and Toggle Quantize. These
start unassigned; BPM Value has no shortcut action. They share mouse-button
guards and settings, including zoom-scaled grid nudges and Shift’s triple-size step (1/3 ms at
1152–1500 points/s). Memory numbers sort chronologically; Hot letters
stay fixed. Creation remains inert on release even if its save finishes while
the key is held. A held active-page cue remembers its original page and
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
New Command-number defaults yield to preexisting explicit custom bindings;
the existing action remains active until the user explicitly moves its shortcut.
Restore reloads both the editor and the router; no separate keyboard file can
silently override restored user state.

Tests cover conflict moves, persistence/unbinding/reset, modifier/focus changes,
autorepeat, multiple holds, page switching, failed-save rollback and text-focus
boundaries. Source changes require the full release/render gate and installed
native-player acceptance; see [HANDOFF](HANDOFF.md) for the current state and
[PLAYER](PLAYER.md) for the cue editing/transport contract.

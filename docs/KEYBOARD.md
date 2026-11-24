# Keyboard settings

All six Settings tabs, including Keyboard, are prepared before the main window's
first show. Opening Keyboard uses those ready controls and current saved mappings;
it does not build the editor on the first click.

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
For a focused playlist/folder sidebar row, Rename edits its plain name inline;
Enter saves and Escape cancels. Icons/counts stay outside the editor.
**Library → Select all** defaults to Command-A and selects the current song
browser, including when focus is in the playlist sidebar. It can be rebound or
unassigned. Plain A and Command-A are distinct; text editors keep native select
all. The new default yields to any existing explicitly assigned Command-A action.
Graveyard’s standard Cmd+A selects all before configurable grid actions.
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
the nearest current beat. Existing cues always trigger their exact saved positions.
Memory Cue recall also sets the regular temporary CUE anchor to that exact saved
position; it does not quantize or create a cue. Hot cues keep ordinary
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

**Load selected track** follows the active visible browser selection, including
Artists; a hidden/filtered song cannot supply that action. Play/Pause still
operates the already-loaded deck independently of browser selection.

Bindings operate inside SoundBox from the normal browsing surfaces. Search,
inline editors, ordinary dialogs, settings lists and device filesystem browsing
retain normal typing/navigation. Open/close Settings also works from nontext
settings controls. Graveyard additionally accepts playback, existing cues,
wave/grid/autoplay controls and Load for its own selection; it keeps native
multi-selection, captures no queue and does not enable new cue creation/deletion.
These are application mappings, not global OS hotkeys. Native
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
boundaries. Local changes use affected keyboard/player/prep checks and Cocoa
interaction evidence; complete release/render validation remains required before
publication. Native-player acceptance retains its separate scope; see
[HANDOFF](HANDOFF.md) for the current state and
[PLAYER](PLAYER.md) for the cue editing/transport contract.

The installed November1 follow-up adds unassigned **Set cue** directly below
**Toggle cue page**.
It captures at the current playhead into the first empty slot on the active Hot
or Memory page, with the existing Quantize and durable-save rules. It never jumps
to or overwrites a filled slot, and a held key does not repeat capture. Assign it
in Settings → Keyboard without displacing an existing shortcut. **Focus search**
also works from Graveyard; its global query filters the Graveyard list. Restore
keeps the visible list anchor. Holding the on-screen left/right grid arrows repeats
shifts until release; other grid buttons remain single action.

Held grid-left/right shortcuts now repeat after300ms at80ms intervals, sharing
one timer across aliases and using the same durable edit path as the buttons.
Release, deactivation, text/modal focus, a different loaded track or an edit error
stops repetition. Other single-press commands retain their existing behavior.

Jump backward/forward shortcuts are physical holds: immediate jump,300ms delay,
then100ms repeats until release, focus loss or track change. Oversized jumps use
the remaining audio and stop at0/duration, with or without a native beat grid.

November1 playlist follow-up: Up/Down from the playlist sidebar route into the
active track table, preserving Shift selection; they never choose another playlist.
Session CUE (its button and mapped keys) cannot take over while a Hot or Memory
Cue is physically held. Grid-manifest arrival refreshes the bar-start controls
independently of audio readiness, preserving the user’s Command-A mapping.

November3 lookup refinement shares canonical string parsing only inside one
current-map lookup. It retains mutable overrides, default-delete conflict rules,
ambiguous-map refusal, press/release ownership and fresh Qt parser behavior;
it does not persist a compiled map or change any binding/default.

The November4–5 application event-filter refinement delegates unrelated layout/
style events immediately and reads each event's type once. Shortcut overrides,
press/release, deactivation and normal-quit save draining retain their existing
routes; this optimization adds no shortcuts or source-write capability.

"""One action catalog and keymap for the settings editor and press/release router."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import copy

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence


@dataclass(frozen=True)
class Command:
    id: str
    group: str
    label: str
    defaults: tuple[str, ...] = ()
    hold: bool = False


COMMANDS = (
    Command("transport.play", "Transport", "Play / pause", ("Space",)),
    Command("transport.cue", "Transport", "Session CUE", ("C",), True),
    Command("transport.previous", "Transport", "Previous track"),
    Command("transport.next", "Transport", "Next track"),
    Command("transport.back", "Transport", "Jump backward"),
    Command("transport.forward", "Transport", "Jump forward"),
    Command("cues.hot_page", "Cue pages", "Show Hot Cues", ("9",)),
    Command("cues.memory_page", "Cue pages", "Show Memory Cues", ("0",)),
    *(Command(f"cues.slot.{i}", "Cue pages", f"Cue {i + 1} · active page", (str(i + 1),), True)
      for i in range(8)),
    *(Command(f"hot.{i}", "Direct Hot Cues", f"Hot Cue {chr(65 + i)}", hold=True) for i in range(8)),
    *(Command(f"memory.{i}", "Direct Memory Cues", f"Memory Cue {i + 1}", hold=True) for i in range(8)),
    Command("browser.rename", "Library", "Rename selected item", ("Ctrl+R",)),
    Command("browser.load", "Library", "Load selected track"),
    Command("browser.search", "Library", "Focus search"),
    Command("browser.playing", "Library", "Show playing track"),
    Command("library.refresh", "Library", "Refresh rekordbox library"),
    Command("library.playlist", "Library", "New SoundBox playlist"),
    Command("library.import", "Library", "Import audio files"),
    Command("app.settings", "Application", "Open / close Settings"),
    Command("wave.zoom_in", "Waveform", "Zoom in"),
    Command("wave.zoom_out", "Waveform", "Zoom out"),
    *(Command(f"wave.{mode}", "Waveform", f"{mode} display") for mode in ("BLUE", "RGB", "3BAND")),
    *(Command(f"autoplay.{mode}", "Autoplay", label) for mode, label in (
        ("up", "Autoplay upward"), ("random", "Autoplay random"), ("down", "Autoplay downward"))),
    Command("autoplay.off", "Autoplay", "Turn autoplay off"),
)
BY_ID = {command.id: command for command in COMMANDS}
_MODIFIERS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
              Qt.Key_AltGr, Qt.Key_unknown}


def canonical(binding: str) -> str:
    sequence = QKeySequence.fromString(binding, QKeySequence.PortableText)
    if sequence.count() != 1 or sequence[0].key() in _MODIFIERS:
        return ""
    return sequence.toString(QKeySequence.PortableText)


def display(binding: str) -> str:
    return QKeySequence(binding).toString(QKeySequence.NativeText)


def event_binding(event) -> str:
    if event.key() in _MODIFIERS:
        return ""
    return QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)


class Keymap:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state) if isinstance(state, dict) else {}
        self.editable = self.state.get("schema", 1) == 1
        raw = self.state.get("bindings", {}) if self.editable else {}
        self.overrides = raw if isinstance(raw, dict) else {}

    def bindings(self, action: str) -> list[str]:
        raw = self.overrides.get(action, BY_ID[action].defaults)
        if not isinstance(raw, (list, tuple)):
            raw = BY_ID[action].defaults
        return list(dict.fromkeys(canonical(value) for value in raw
                                  if isinstance(value, str) and canonical(value)))

    def conflicts(self, binding: str, action: str = "") -> list[str]:
        return [command.id for command in COMMANDS
                if command.id != action and binding in self.bindings(command.id)]

    def action(self, binding: str) -> str | None:
        found = self.conflicts(binding)
        return found[0] if len(found) == 1 else None  # corrupt/ambiguous saved maps fail closed

    def assign(self, action: str, binding: str, *, replace: str = "", move=False):
        if not self.editable:
            raise ValueError("This keymap uses a newer format. Existing settings were preserved.")
        binding = canonical(binding)
        if action not in BY_ID or not binding:
            raise ValueError("Choose a key with optional modifiers.")
        conflicts = self.conflicts(binding, action)
        if conflicts and not move:
            raise ValueError("Already assigned to " + ", ".join(BY_ID[item].label for item in conflicts))
        for other in conflicts:
            self.overrides[other] = [key for key in self.bindings(other) if key != binding]
        keys = [key for key in self.bindings(action) if key != replace]
        self.overrides[action] = list(dict.fromkeys([*keys, binding]))

    def remove(self, action: str, binding: str):
        if not self.editable:
            raise ValueError("This keymap uses a newer format. Existing settings were preserved.")
        self.overrides[action] = [key for key in self.bindings(action) if key != binding]

    def reset(self, action: str | None = None):
        if not self.editable:
            raise ValueError("This keymap uses a newer format. Existing settings were preserved.")
        if action:
            for binding in BY_ID[action].defaults:
                conflicts = self.conflicts(binding, action)
                if conflicts:
                    raise ValueError(display(binding) + " is assigned to " + BY_ID[conflicts[0]].label)
            self.overrides.pop(action, None)
        else:
            for command in COMMANDS:
                self.overrides.pop(command.id, None)

    def dump(self) -> dict:
        if not self.editable:
            return copy.deepcopy(self.state)
        return {**self.state, "schema": 1, "bindings": copy.deepcopy(self.overrides)}


class KeyboardRouter:
    """Track physical keys so modifier/focus/page changes cannot strand a cue."""
    def __init__(self, dispatch: Callable[[str], Callable[[], None] | None]):
        self.keymap = Keymap()
        self.dispatch = dispatch
        self.held: dict[int, tuple[str, Callable[[], None]]] = {}

    def release_all(self):
        releases = {id(release): release for _action, release in self.held.values()}
        self.held.clear()
        for release in releases.values():
            release()

    def handle(self, event, allowed: bool) -> bool:
        physical = event.nativeScanCode() or event.key()
        if event.type() == QEvent.KeyRelease and physical in self.held:
            if not event.isAutoRepeat():
                action, release = self.held.pop(physical)
                if not any(other_release is release for _other, other_release in self.held.values()):
                    release()
            return True
        action = self.keymap.action(event_binding(event)) if allowed else None
        if not action:
            return False
        if event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.KeyPress:
            if event.isAutoRepeat() or physical in self.held:
                return True
            if BY_ID[action].hold:
                existing = (None if action.startswith("cues.slot.") else next(
                    (release for other, release in self.held.values() if other == action), None))
                release = existing or self.dispatch(action)
                if release:
                    self.held[physical] = (action, release)
            else:
                self.dispatch(action)
            return True
        return False

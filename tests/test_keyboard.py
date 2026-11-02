"""Keymap persistence/conflicts and physical press/release routing; no audio."""
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from desktop.keyboard import BY_ID, COMMANDS, Keymap, KeyboardRouter


def key(kind, code, modifiers=Qt.NoModifier, repeat=False):
    return QKeyEvent(kind, code, modifiers, "", repeat)


class Keymaps(unittest.TestCase):
    def test_set_cue_follows_page_toggle_and_does_not_displace_existing_bindings(self):
        ids=[c.id for c in COMMANDS];self.assertEqual(ids[ids.index('cues.toggle_page')+1],'cues.set')
        mapping=Keymap();self.assertEqual(mapping.bindings('cues.set'),[])
        mapping.assign('cues.set','V');self.assertEqual(Keymap(mapping.dump()).action('V'),'cues.set')
        self.assertEqual(mapping.action('C'),'transport.cue')

    def test_defaults_are_unique_and_active_page_slots_have_requested_numbers(self):
        mapping = Keymap()
        self.assertEqual(len(BY_ID), len(COMMANDS))
        self.assertEqual(mapping.bindings("transport.play"), ["Space"])
        self.assertEqual(mapping.bindings("transport.cue"), ["C"])
        for i in range(8):
            self.assertEqual(mapping.action(str(i + 1)), f"cues.slot.{i}")
            self.assertEqual(mapping.action(f'Ctrl+{i + 1}'), f'cues.delete.{i}')
        self.assertEqual(mapping.action("9"), "cues.hot_page")
        self.assertEqual(mapping.action("0"), "cues.memory_page")
        self.assertEqual(mapping.action("="), "wave.zoom_in")
        self.assertEqual(mapping.action("-"), "wave.zoom_out")
        for command in COMMANDS:
            for binding in mapping.bindings(command.id):
                self.assertEqual(mapping.conflicts(binding), [command.id])

    def test_conflict_requires_explicit_move_and_multiple_bindings_roundtrip(self):
        mapping = Keymap({"schema": 1, "future": "keep", "bindings": {"future.action": ["F12"]}})
        with self.assertRaises(ValueError):
            mapping.assign("transport.cue", "Space")
        mapping.assign("transport.cue", "Space", move=True)
        mapping.assign("transport.cue", "Ctrl+K")
        restored = Keymap(mapping.dump())
        self.assertEqual(restored.bindings("transport.play"), [])
        self.assertEqual(restored.bindings("transport.cue"), ["C", "Space", "Ctrl+K"])
        self.assertEqual(restored.dump()["future"], "keep")
        self.assertEqual(restored.dump()["bindings"]["future.action"], ["F12"])
        restored.reset()
        self.assertEqual(restored.bindings("transport.play"), ["Space"])
        self.assertEqual(restored.dump()["bindings"]["future.action"], ["F12"])

    def test_unbinding_persists_and_conflicting_corrupt_maps_do_not_execute(self):
        mapping = Keymap()
        mapping.remove("transport.cue", "C")
        self.assertEqual(Keymap(mapping.dump()).bindings("transport.cue"), [])
        corrupt = Keymap({"schema": 1, "bindings": {"transport.play": ["C"]}})
        self.assertIsNone(corrupt.action("C"))
        self.assertEqual(Keymap({"schema": 1, "bindings": {"transport.cue": ["Ctrl+K, Ctrl+C"]}}).bindings("transport.cue"), [])

    def test_reset_one_does_not_silently_steal_a_customized_binding(self):
        mapping = Keymap()
        mapping.assign("transport.play", "C", move=True)
        with self.assertRaises(ValueError):
            mapping.reset("transport.cue")
        self.assertEqual(mapping.action("C"), "transport.play")

    def test_retired_direct_cue_bindings_are_preserved_but_never_execute(self):
        legacy = {"hot.0": ["F1"], "memory.0": ["F2"]}
        mapping = Keymap({"schema": 1, "bindings": legacy})
        self.assertNotIn("Direct Hot Cues", {command.group for command in COMMANDS})
        self.assertNotIn("Direct Memory Cues", {command.group for command in COMMANDS})
        self.assertIsNone(mapping.action("F1"))
        self.assertIsNone(mapping.action("F2"))
        self.assertEqual(mapping.action("1"), "cues.slot.0")
        self.assertEqual(mapping.dump()["bindings"], legacy)
        mapping.assign("cues.slot.0", "F1")
        self.assertEqual(mapping.action("F1"), "cues.slot.0")
        self.assertEqual(mapping.dump()["bindings"]["hot.0"], ["F1"])

    def test_new_delete_defaults_preserve_existing_explicit_shortcuts(self):
        mapping = Keymap({'schema': 1, 'bindings': {'browser.search': ['Ctrl+1']}})
        self.assertEqual(mapping.action('Ctrl+1'), 'browser.search')
        self.assertEqual(mapping.bindings('cues.delete.0'), [])
        mapping.assign('cues.delete.0', 'Ctrl+1', move=True)
        self.assertEqual(mapping.action('Ctrl+1'), 'cues.delete.0')


class KeyboardRouting(unittest.TestCase):
    def setUp(self):
        self.events = []
        def dispatch(action):
            self.events.append(("press", action))
            return lambda: self.events.append(("release", action))
        self.router = KeyboardRouter(dispatch)

    def test_rebound_hold_releases_when_focus_or_modifier_changes(self):
        self.router.keymap.remove("transport.cue", "C")
        self.router.keymap.assign("transport.cue", "Ctrl+K")
        self.assertFalse(self.router.handle(key(QEvent.KeyPress, Qt.Key_C), True))
        self.assertTrue(self.router.handle(key(QEvent.KeyPress, Qt.Key_K, Qt.ControlModifier), True))
        self.assertTrue(self.router.handle(key(QEvent.KeyRelease, Qt.Key_K), False))
        self.assertEqual(self.events, [("press", "transport.cue"), ("release", "transport.cue")])

    def test_aliases_share_a_hold_and_repeat_release_does_not_end_it(self):
        self.router.keymap.assign("transport.cue", "V")
        for code in (Qt.Key_C, Qt.Key_V):
            self.router.handle(key(QEvent.KeyPress, code), True)
        self.router.handle(key(QEvent.KeyRelease, Qt.Key_C, repeat=True), True)
        self.assertEqual(self.events, [("press", "transport.cue")])
        self.router.handle(key(QEvent.KeyRelease, Qt.Key_C), True)
        self.assertEqual(self.events, [("press", "transport.cue")])
        self.router.handle(key(QEvent.KeyRelease, Qt.Key_V), True)
        self.assertEqual(self.events[-1], ("release", "transport.cue"))

    def test_activation_loss_releases_each_control_once_and_typing_is_untouched(self):
        self.assertFalse(self.router.handle(key(QEvent.KeyPress, Qt.Key_C), False))
        self.router.handle(key(QEvent.KeyPress, Qt.Key_C), True)
        self.router.handle(key(QEvent.KeyPress, Qt.Key_1), True)
        self.router.release_all()
        self.router.release_all()
        self.assertEqual(len([edge for edge, _action in self.events if edge == "release"]), 2)
        self.assertEqual(self.router.held, {})

    def test_shortcut_override_never_triggers_the_command(self):
        override = key(QEvent.ShortcutOverride, Qt.Key_C)
        self.assertTrue(self.router.handle(override, True))
        self.assertTrue(override.isAccepted())
        self.assertEqual(self.events, [])

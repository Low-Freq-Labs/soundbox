"""Operation-local parsing keeps current custom bindings and ambiguity intact."""
import random
import unittest
from unittest import mock

from desktop import keyboard


class KeymapLookupWork(unittest.TestCase):
    def test_each_current_text_is_parsed_once_per_conflict_lookup(self):
        mapping = keyboard.Keymap({'bindings': {'transport.cue': ['C', 'Ctrl+K'],
                                               'browser.search': ['Ctrl+1']}})
        original = keyboard.canonical
        with mock.patch.object(keyboard, 'canonical', wraps=original) as parser:
            self.assertEqual(mapping.action('Ctrl+K'), 'transport.cue')
        values = [call.args[0] for call in parser.call_args_list]
        self.assertEqual(len(values), len(set(values)))
        mapping.overrides['transport.play'] = ['Ctrl+K']
        self.assertIsNone(mapping.action('Ctrl+K'))
        mapping.overrides['transport.play'].clear()
        self.assertEqual(mapping.action('Ctrl+K'), 'transport.cue')

    def test_lookup_matches_independent_per_action_results_for_mutable_maps(self):
        rng = random.Random(20261103)
        choices = ['C', 'Space', 'Ctrl+1', 'Shift+F1', 'Ctrl+K, Ctrl+C', '', 'Ctrl', None]
        mapping = keyboard.Keymap()
        for _ in range(200):
            command = rng.choice(keyboard.COMMANDS)
            mapping.overrides[command.id] = [rng.choice(choices) for _ in range(rng.randrange(4))]
            for binding in ('C', 'Space', 'Ctrl+1', 'Shift+F1'):
                expected = [c.id for c in keyboard.COMMANDS if binding in mapping.bindings(c.id)]
                self.assertEqual(mapping.conflicts(binding), expected)
                self.assertEqual(mapping.action(binding), expected[0] if len(expected) == 1 else None)


if __name__ == '__main__':
    unittest.main()

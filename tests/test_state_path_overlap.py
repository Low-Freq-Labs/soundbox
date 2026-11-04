"""Publication overlap rejection keeps its exact lexical boundary and guards."""
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock

from soundbox.state_io import StateArea, _reject_overlapping_paths


class StatePublicationPaths(unittest.TestCase):
    def test_random_path_sets_match_original_pair_predicate_in_both_orders(self):
        rng = random.Random(20261103)
        for _ in range(500):
            paths = [Path('/fixture') / '/'.join(rng.choices(('a', 'ab', 'b', 'c'),
                     k=rng.randrange(1, 5))) for _ in range(rng.randrange(12))]
            expected = any(p in q.parents or q in p.parents
                           for i, p in enumerate(paths) for q in paths[i+1:])
            for selected in (paths, list(reversed(paths))):
                if expected:
                    with self.assertRaisesRegex(ValueError, 'Overlapping'):
                        _reject_overlapping_paths(selected)
                else:
                    _reject_overlapping_paths(selected)
        # This optimization does not introduce case folding or a new alias
        # policy; it retains the original proper-ancestor predicate exactly.
        _reject_overlapping_paths([Path('/fixture/a'), Path('/fixture/a')])
        _reject_overlapping_paths([Path('/fixture/a'), Path('/fixture/ab/file')])

    def test_parent_and_child_are_rejected_before_tree_walk_or_journal_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            area = StateArea(root)
            (root / 'art').mkdir()
            (root / 'art/cover.jpg').write_bytes(b'original')
            for entries in ((('art', None), ('art/cover.jpg', b'new')),
                            (('art/cover.jpg', b'new'), ('art', None))):
                with mock.patch('soundbox.state_io.plain_tree',
                                side_effect=AssertionError('Tree walked before overlap rejection')):
                    with self.assertRaisesRegex(ValueError, 'Overlapping'):
                        area.publish(dict(entries))
                self.assertEqual((root / 'art/cover.jpg').read_bytes(), b'original')
                self.assertFalse(area.journal.exists())

    def test_large_sibling_batch_checks_only_ancestor_prefixes(self):
        class CountedPath(type(Path())):
            visited = 0
            @property
            def parents(self):
                type(self).visited += 1
                return super().parents
        paths = [CountedPath('/fixture/art') / f'{i}.jpg' for i in range(2000)]
        CountedPath.visited = 0
        _reject_overlapping_paths(paths)
        self.assertEqual(CountedPath.visited, len(paths))


if __name__ == '__main__':
    unittest.main()

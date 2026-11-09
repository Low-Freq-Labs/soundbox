"""Maintainability checks enforce boundaries without importing application code."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_layout import anchors, authored_files, check, dependency_errors, link_errors


class RepositoryLayout(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.write('README.md', '# Project\n')
        self.write('AGENTS.md', 'Shared rules\n')
        self.write('CLAUDE.md', 'Shared rules\n')

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return Path(path)

    def test_untracked_authored_folder_needs_a_short_guide(self):
        self.write('soundbox/model.py', 'VALUE = 1\n')
        self.assertIn('soundbox: missing README.md', check(self.root))
        self.write('soundbox/README.md', '# Engine\n')
        self.assertEqual(check(self.root), [])
        self.write('soundbox/README.md', '# Engine\n' + 'Extra\n' * 25)
        self.assertIn('soundbox/README.md: keep the folder guide within 25 lines', check(self.root))

    def test_ignored_generated_and_vendor_folders_are_excluded(self):
        self.write('.gitignore', 'build/\n')
        self.write('build/native/output.h', '// generated\n')
        self.write('native/README.md', '# Native\n')
        self.write('native/cdj/README.md', '# CDJ\n')
        self.write('native/cdj/vendor/upstream/code.rs', '// pinned upstream\n')
        paths = authored_files(self.root)
        self.assertNotIn(Path('build/native/output.h'), paths)
        self.assertNotIn(Path('native/cdj/vendor/upstream/code.rs'), paths)
        self.assertEqual(check(self.root), [])

    def test_instruction_files_must_match(self):
        self.write('CLAUDE.md', 'Different rules\n')
        self.assertIn('AGENTS.md and CLAUDE.md differ', check(self.root))

    def test_engine_is_qt_free_and_production_cannot_import_research_or_tests(self):
        paths = [self.write('soundbox/bad.py', 'from PySide6.QtCore import QObject\nimport desktop.app\n'),
                 self.write('desktop/bad.py', 'from research import prototype\nimport tests.test_ui\n'),
                 self.write('desktop/personal.py', 'import soundbox_personal.discovery\n')]
        errors = dependency_errors(self.root, paths)
        self.assertEqual(len(errors), 5)
        self.assertTrue(any('Qt-free engine imports PySide6' in error for error in errors))
        self.assertTrue(any('production imports tests' in error for error in errors))
        self.assertTrue(any('use provider admission' in error for error in errors))

    def test_relative_engine_imports_and_personal_adapters_are_allowed(self):
        paths = [self.write('soundbox/good.py', 'from . import library\n'),
                 self.write('soundbox_personal/good.py', 'from soundbox import providers\n'),
                 self.write('desktop/good.py', 'from PySide6 import QtCore\n')]
        self.assertEqual(dependency_errors(self.root, paths), [])

    def test_links_include_references_and_duplicate_heading_anchors(self):
        paths = [self.write('README.md', '[Guide](docs/guide.md#second-copy-1)\n[ref]: docs/guide.md#second-copy\n'),
                 self.write('docs/guide.md', '# Guide\n## Second copy\n## Second copy\n')]
        self.assertEqual(link_errors(self.root, paths), [])
        self.write('README.md', '[Bad](docs/guide.md#missing)\n[Bad](absent.md)\n')
        errors = link_errors(self.root, paths)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any('missing heading' in error for error in errors))
        self.assertTrue(any('missing link' in error for error in errors))

    def test_code_examples_and_external_urls_are_not_local_links(self):
        path = self.write('README.md', '```md\n[Example](absent.md)\n```\n[Website](https://example.com)\n')
        self.assertEqual(link_errors(self.root, [path]), [])
        self.assertEqual(anchors('# Real\n```md\n# Example\n```\n'), {'real'})

    def test_path_escape_is_rejected(self):
        path = self.write('README.md', '[Outside](../private.md)\n')
        self.assertTrue(any('link leaves checkout' in error for error in link_errors(self.root, [path])))


if __name__ == '__main__':
    unittest.main()

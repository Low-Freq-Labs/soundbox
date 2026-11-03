"""Archive checks prove capabilities and code, including hidden PYZ contents."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.build_composition import REQUIRED_RUNTIME_MODULES, verify_runtime_code, composition


class PackagedRuntimeCode(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.code = {}
        for name in REQUIRED_RUNTIME_MODULES:
            path = self.root / (name.replace('.', '/') + '.py')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('def capability(value):\n    return value + 1\n')
            self.code[name] = compile(path.read_bytes(), 'bundled/' + name, 'exec', dont_inherit=True)

    def verify(self):
        return verify_runtime_code(SimpleNamespace(toc=self.code, extract=self.code.__getitem__), root=self.root)

    def test_every_required_capability_and_nested_code_match_despite_bundle_paths(self):
        self.assertEqual(self.verify(), sorted(REQUIRED_RUNTIME_MODULES))
        self.assertTrue({'research', 'tests'} <= set(composition('personal')['excludes']))
        self.assertTrue({'research', 'tests'} <= set(composition('public')['excludes']))

    def test_missing_cbr_capability_cannot_pass_a_matching_profile_manifest(self):
        del self.code['soundbox.rekordbox_cbr_analysis_store']
        with self.assertRaisesRegex(ValueError, 'Shared runtime modules missing'):
            self.verify()

    def test_nested_implementation_mismatch_and_missing_source_fail(self):
        name = 'soundbox.mp3_scan'
        original = self.code[name]
        self.code[name] = compile('def capability(value):\n    return value + 2\n', 'bundled', 'exec')
        with self.assertRaisesRegex(ValueError, 'differs from tested source'):
            self.verify()
        self.code[name] = original
        (self.root / 'soundbox/mp3_scan.py').unlink()
        with self.assertRaisesRegex(ValueError, 'has no source'):
            self.verify()

    def test_developer_modules_hidden_only_inside_pyz_are_rejected(self):
        for name in ('research', 'research.native_library.mp3_analysis_pilot', 'tests.test_cbr_analysis_store'):
            with self.subTest(module=name):
                self.code[name] = compile('', name, 'exec')
                with self.assertRaisesRegex(ValueError, 'Developer Python modules leaked'):
                    self.verify()
                del self.code[name]

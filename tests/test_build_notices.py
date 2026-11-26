"""Generated wheel notices cannot redirect the collector into unrelated data."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import build_notices as notices


class NoticeMetadata(dict):
    def get_all(self, key):
        return []


class RuntimeNotices(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='soundbox-notice-test-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.wheel = self.root / 'wheel'; self.wheel.mkdir()
        self.foreign = self.root / 'unrelated.txt'; self.foreign.write_bytes(b'private generated sentinel')
        self.stdlib = self.root / 'stdlib'; self.stdlib.mkdir()
        (self.stdlib / 'LICENSE.txt').write_bytes(b'Python fixture license')
        assets = self.root / 'assets/licenses'; assets.mkdir(parents=True)
        for name in ('GPL-3.0-only.txt', 'LGPL-3.0-only.txt'):
            (assets / name).write_bytes(name.encode())
        self.app = self.root / 'Fixture.app'
        self.output = self.app / 'Contents/Resources/licenses/runtime'

    def collect(self, files=('licenses/LICENSE.txt',), version='1.2.3'):
        dist = SimpleNamespace(version=version, files=files,
            metadata=NoticeMetadata(Name='Example', License='Generated license metadata'),
            locate_file=lambda relative: self.wheel / str(relative))
        boot = SimpleNamespace(version='6.0', files=(),
            metadata=NoticeMetadata(Name='PyInstaller', License='Generated bootloader metadata'),
            locate_file=lambda relative: self.wheel / str(relative))
        with mock.patch.object(notices.metadata, 'packages_distributions', return_value={'example': ['example']}), \
                mock.patch.object(notices.metadata, 'distribution', side_effect=lambda name: boot if name == 'pyinstaller' else dist), \
                mock.patch.object(notices.sysconfig, 'get_path', return_value=str(self.stdlib)), \
                mock.patch.object(notices, 'ROOT', self.root):
            return notices.collect(['example.module'], self.output)

    def license(self):
        path = self.wheel / 'licenses/LICENSE.txt'; path.parent.mkdir()
        path.write_bytes(b'Generated public license text')
        return path

    def inventory(self, transform):
        path = self.output / 'inventory.json'
        value = json.loads(path.read_bytes()); transform(value)
        path.write_text(json.dumps(value))

    def test_collected_exact_bytes_inventory_and_internal_framework_alias_verify(self):
        source = self.license(); self.collect()
        self.assertEqual((self.output / 'Example-1.2.3/licenses/LICENSE.txt').read_bytes(), source.read_bytes())
        result = notices.verify(self.app)
        self.assertEqual(result['files'], 6)
        moved = self.app / 'Contents/Frameworks/licenses'
        moved.parent.mkdir(parents=True)
        self.output.parent.rename(moved)
        self.output.parent.symlink_to('../Frameworks/licenses', target_is_directory=True)
        self.assertEqual(notices.verify(self.app), result)

    def test_leaf_and_parent_redirects_refuse_without_copying_unrelated_bytes(self):
        for parent_link in (False, True):
            with self.subTest(parent_link=parent_link):
                target = self.wheel / 'licenses'
                if parent_link:
                    foreign_dir = self.root / 'foreign-dir'; foreign_dir.mkdir()
                    (foreign_dir / 'LICENSE.txt').write_bytes(self.foreign.read_bytes())
                    target.symlink_to(foreign_dir, target_is_directory=True)
                else:
                    target.mkdir(); (target / 'LICENSE.txt').symlink_to(self.foreign)
                with self.assertRaises((ValueError, OSError)):
                    self.collect()
                self.assertEqual(self.foreign.read_bytes(), b'private generated sentinel')
                self.assertFalse(any(path.read_bytes() == self.foreign.read_bytes()
                    for path in self.output.rglob('*') if path.is_file()))
                if parent_link:
                    target.unlink()
                else:
                    (target / 'LICENSE.txt').unlink(); target.rmdir()
                # Each refusal leaves only its own incomplete output.
                import shutil
                shutil.rmtree(self.output)

    def test_escaping_record_and_version_filename_refuse(self):
        for files, version in ((('../LICENSE.txt',), '1.0'), ((), '../../outside')):
            with self.subTest(files=files, version=version), self.assertRaises(ValueError):
                self.collect(files, version)
            import shutil
            shutil.rmtree(self.output)
        self.assertFalse((self.root / 'outside').exists())

    def test_changed_file_and_replaced_ancestor_during_read_refuse(self):
        source = self.license(); original = os.read
        for replace_parent in (False, True):
            changed = False
            def read(descriptor, size):
                nonlocal changed
                raw = original(descriptor, size)
                if raw and not changed:
                    changed = True
                    if replace_parent:
                        source.parent.rename(self.wheel / 'old-licenses')
                        source.parent.mkdir(); source.write_bytes(b'replacement')
                    else:
                        source.write_bytes(b'changed during read')
                return raw
            with self.subTest(replace_parent=replace_parent), mock.patch.object(notices.os, 'read', read), \
                    self.assertRaises(ValueError):
                notices._read_regular(self.wheel, 'licenses/LICENSE.txt')

    def test_oversized_source_and_hardlinked_notice_refuse(self):
        source = self.license()
        with self.assertRaises(ValueError):
            notices._read_regular(self.wheel, 'licenses/LICENSE.txt', limit=4)
        os.link(source, self.wheel / 'second-name')
        with self.assertRaises(ValueError):
            self.collect()

    def test_inventory_rejects_modified_missing_extra_escaping_and_duplicate_entries(self):
        self.license(); self.collect()
        original = {path: path.read_bytes() for path in self.output.rglob('*') if path.is_file()}
        path = self.output / 'Python-LICENSE.txt'
        for change in ('modified', 'missing', 'extra', 'escaping', 'duplicate'):
            with self.subTest(change=change):
                if change == 'modified': path.write_bytes(b'different')
                elif change == 'missing': path.unlink()
                elif change == 'extra': (self.output / 'unlisted.txt').write_bytes(b'unlisted')
                elif change == 'escaping':
                    self.inventory(lambda value: value['sha256'].update({'../unrelated.txt': 'a'*64}))
                else:
                    inventory = self.output / 'inventory.json'
                    inventory.write_bytes(inventory.read_bytes().replace(b'"format":', b'"format":"wrong","format":', 1))
                with self.assertRaises((ValueError, OSError)):
                    notices.verify(self.app)
                (self.output / 'unlisted.txt').unlink(missing_ok=True)
                for target, raw in original.items(): target.write_bytes(raw)

    def test_external_notice_and_root_links_refuse_before_foreign_file_read(self):
        self.license(); self.collect()
        path = self.output / 'Python-LICENSE.txt'; path.unlink(); path.symlink_to(self.foreign)
        self.inventory(lambda value: value['sha256'].update({'Python-LICENSE.txt': hashlib.sha256(self.foreign.read_bytes()).hexdigest()}))
        with mock.patch.object(notices, '_read_regular', wraps=notices._read_regular) as read:
            with self.assertRaisesRegex(ValueError, 'escapes'):
                notices.verify(self.app)
            self.assertTrue(all((Path(call.args[0]) / str(call.args[1])).resolve()
                                != self.foreign for call in read.call_args_list))
        self.assertEqual(self.foreign.read_bytes(), b'private generated sentinel')
        outside = self.root / 'outside-notices'; self.output.rename(outside)
        self.output.symlink_to(outside, target_is_directory=True)
        with mock.patch.object(notices, '_read_regular') as read:
            with self.assertRaisesRegex(ValueError, 'escape'):
                notices.verify(self.app)
            read.assert_not_called()

    def test_actual_installed_distribution_collector_produces_verifiable_notices(self):
        # Actual installed wheel RECORDs and public license assets, no user state.
        notices.collect(['PIL.Image'], self.output)
        result = notices.verify(self.app)
        self.assertGreater(result['files'], 4)
        value = json.loads((self.output / 'inventory.json').read_bytes())
        self.assertTrue({'pillow', 'pyinstaller'} <= {row['name'].casefold() for row in value['distributions']})

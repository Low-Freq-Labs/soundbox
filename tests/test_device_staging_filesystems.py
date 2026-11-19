"""Real generated FAT32/exFAT images; never discover or modify physical disks."""
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import device_staging, manifest
from soundbox.cdj_transaction import CdjSession
from soundbox.sync import DeviceSession


class DeviceStagingFilesystems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # mkdtemp has no destructor that could delete a still-mounted backing
        # image after a failed detach. Retain the entire area on cleanup error.
        cls.area = Path(tempfile.mkdtemp(prefix='sb-staging-filesystems-')).resolve()
        cls.images = []
        cls.addClassCleanup(cls._retire_images)
        for name, filesystem in (('fat32', 'MS-DOS FAT32'), ('exfat', 'ExFAT')):
            image = cls.area / (name + '.dmg')
            mount = cls.area / (name + '-mount')
            mount.mkdir()
            fixture = dict(name=name, image=image, mount=mount, mounted=False,
                           uncertain_attachment=True)
            cls.images.append(fixture)
            cls._command('create', '-size', '128m', '-fs', filesystem,
                         '-layout', 'NONE', '-volname', 'SBTEST' + name.upper(),
                         '-nospotlight', str(image))
            with image.open('rb') as source:
                boot = source.read(512)
            signature = boot[82:90] if name == 'fat32' else boot[3:11]
            if signature != (b'FAT32   ' if name == 'fat32' else b'EXFAT   '):
                raise AssertionError('Generated image has the wrong filesystem')
            # Creation can format through a temporary attachment too. Retain
            # uncertainty until the requested private mount is fully validated.
            try:
                attached = cls._command('attach', '-nobrowse', '-noautoopen',
                                        '-mountpoint', str(mount), '-plist', str(image))
            finally:
                # Even an attach/parser error must retire a partial own mount.
                fixture['mounted'] = mount.stat().st_dev != cls.area.stat().st_dev
            entities = plistlib.loads(attached)['system-entities']
            matches = [row for row in entities if row.get('mount-point') == str(mount)]
            if len(matches) != 1 or not fixture['mounted']:
                raise AssertionError('Generated image was not mounted at its private path')
            if matches[0].get('volume-kind') != ('msdos' if name == 'fat32' else 'exfat'):
                raise AssertionError('Attached image has the wrong filesystem kind')
            fixture['identity'] = (mount.stat().st_dev, mount.stat().st_ino)
            fixture['uncertain_attachment'] = False

    @staticmethod
    def _command(*arguments):
        result = subprocess.run(['/usr/bin/hdiutil', *arguments],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr.decode(errors='replace')[-2000:])
        return result.stdout

    @classmethod
    def _retire_images(cls):
        errors = []
        for fixture in reversed(cls.images):
            if fixture['uncertain_attachment']:
                errors.append(f'Attachment state is uncertain for {fixture["image"]}')
                continue
            if not fixture['mounted']:
                continue
            try:
                # The exact private mountpoint came from our attach command;
                # never enumerate disks, guess a device node, or force detach.
                mount = fixture['mount']
                if mount.is_symlink() or mount.resolve(strict=True) != mount:
                    raise AssertionError('Generated image mount path was redirected')
                current = mount.stat()
                if (current.st_dev, current.st_ino) != fixture['identity']:
                    raise AssertionError('Generated image mount was replaced; refusing detach')
                cls._command('detach', str(fixture['mount']))
                if fixture['mount'].stat().st_dev != cls.area.stat().st_dev:
                    raise AssertionError('Generated image remains mounted')
                fixture['mounted'] = False
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise AssertionError(f'Image cleanup failed; backing files retained at {cls.area}: {errors}')
        shutil.rmtree(cls.area)

    def targets(self):
        for fixture in self.images:
            mount = fixture['mount']
            self.assertEqual((mount.stat().st_dev, mount.stat().st_ino), fixture['identity'])
            target = mount / self._testMethodName
            target.mkdir()
            sources = self.area / ('sources-' + fixture['name']) / self._testMethodName
            sources.mkdir(parents=True)
            yield fixture['name'], target, sources

    @staticmethod
    def stages(target):
        return {str(path.relative_to(target)): path.read_bytes()
                for path in target.rglob('*.sbpart') if path.is_file()}

    def test_atomic_write_handles_empty_and_first_cluster_allocation(self):
        for filesystem, target, _sources in self.targets():
            with self.subTest(filesystem=filesystem):
                session = DeviceSession(target, [], create=False)
                for name, data in (('empty', b''), ('nonempty', b'generated bytes'),
                                   ('large', b'x' * 65536)):
                    session.atomic_write(target / name, data)
                    self.assertEqual((target / name).read_bytes(), data)
                self.assertEqual(self.stages(target), {})

    def test_atomic_copy_and_concat_publish_exact_generated_bytes(self):
        for filesystem, target, sources in self.targets():
            with self.subTest(filesystem=filesystem):
                first, second = sources / 'first', sources / 'second'
                first.write_bytes(b'a' * 32768)
                second.write_bytes(b'b' * 16384)
                session = DeviceSession(target, [first, second], create=False)
                session.atomic_copy(first, target / 'copy')
                session.atomic_concat([first, second], target / 'concat')
                self.assertEqual((target / 'copy').read_bytes(), first.read_bytes())
                self.assertEqual((target / 'concat').read_bytes(), first.read_bytes() + second.read_bytes())
                self.assertEqual(self.stages(target), {})

    def test_manifest_save_reopens_through_the_same_device_session(self):
        for filesystem, target, _sources in self.targets():
            with self.subTest(filesystem=filesystem):
                session = DeviceSession(target, [], create=False)
                payload = dict(schema=2, playlists={'generated': {'name': 'Generated'}}, files={})
                manifest.save(target, payload, session=session)
                self.assertEqual(manifest.load(target, session=session)['playlists'], payload['playlists'])
                self.assertEqual(self.stages(target), {})

    def test_cdj_write_and_copy_use_real_filesystem_publication(self):
        for filesystem, target, sources in self.targets():
            with self.subTest(filesystem=filesystem):
                source = sources / 'catalog'
                source.write_bytes(b'generated native catalog')
                session = CdjSession(target, [source], token=target.stat().st_dev,
                                     volume_uuid='generated-image-' + filesystem)
                written = target / 'PIONEER/rekordbox/written'
                copied = target / 'PIONEER/rekordbox/copied'
                session.atomic_write(written, b'generated settings')
                session.atomic_copy(source, copied,
                                    expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(written.read_bytes(), b'generated settings')
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                self.assertEqual(self.stages(target), {})

    def test_partial_failure_keeps_destination_and_retires_only_own_stage(self):
        for filesystem, target, sources in self.targets():
            with self.subTest(filesystem=filesystem):
                source, missing = sources / 'source', sources / 'missing'
                source.write_bytes(b'x' * 65536)
                destination = target / 'destination'
                destination.write_bytes(b'prior destination')
                foreign = target / '.soundbox-foreign.sbpart'
                foreign.write_bytes(b'other operation')
                before = self.stages(target)
                session = DeviceSession(target, [source, missing], create=False)
                with self.assertRaises(FileNotFoundError):
                    session.atomic_concat([source, missing], destination)
                self.assertEqual(destination.read_bytes(), b'prior destination')
                self.assertEqual(self.stages(target), before)

    def test_partial_cancel_keeps_destination_and_retires_only_own_stage(self):
        for filesystem, target, sources in self.targets():
            with self.subTest(filesystem=filesystem):
                source = sources / 'source'
                source.write_bytes(b'x' * (2 * 1024 * 1024))
                destination = target / 'PIONEER/rekordbox/destination'
                destination.parent.mkdir(parents=True)
                destination.write_bytes(b'prior destination')
                foreign = destination.parent / '.soundbox-foreign.sbpart'
                foreign.write_bytes(b'other operation')
                before = self.stages(target)
                session = CdjSession(target, [source], token=target.stat().st_dev,
                                     volume_uuid='generated-image-' + filesystem)
                calls = []

                def cancel():
                    calls.append(True)
                    return len(calls) == 2

                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    session.atomic_copy(source, destination, should_cancel=cancel)
                self.assertEqual(len(calls), 2)
                self.assertEqual(destination.read_bytes(), b'prior destination')
                self.assertEqual(self.stages(target), before)

    def test_regular_replacement_of_open_stage_is_neither_published_nor_removed(self):
        for filesystem, target, _sources in self.targets():
            with self.subTest(filesystem=filesystem):
                destination = target / 'destination'
                destination.write_bytes(b'prior destination')
                session = DeviceSession(target, [], create=False)
                owned = device_staging._owned
                replacements = []

                def replace_stage(fd, name, owner):
                    if not replacements:
                        os.unlink(name, dir_fd=fd)
                        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                             0o600, dir_fd=fd)
                        with os.fdopen(descriptor, 'wb') as output:
                            output.write(b'foreign replacement')
                        replacements.append(name)
                    return owned(fd, name, owner)

                with mock.patch.object(device_staging, '_owned', side_effect=replace_stage):
                    with self.assertRaisesRegex(RuntimeError, 'temporary file changed'):
                        session.atomic_write(destination, b'generated desired bytes')
                self.assertEqual(len(replacements), 1)
                self.assertEqual((target / replacements[0]).read_bytes(), b'foreign replacement')
                self.assertEqual(destination.read_bytes(), b'prior destination')


class DeviceImageLifecycle(unittest.TestCase):
    def fixture(self, area, *, uncertain=False, mismatch=False):
        root = Path(area).resolve()
        image = root / 'generated.dmg'
        image.write_bytes(b'generated unmounted fixture')
        mount = root / 'mount'
        mount.mkdir()
        current = mount.stat()
        fixture = dict(image=image, mount=mount, mounted=not uncertain,
                       uncertain_attachment=uncertain,
                       identity=(current.st_dev, current.st_ino + int(mismatch)))
        return SimpleNamespace(area=root, images=[fixture], _command=mock.Mock()), image

    def test_uncertain_attachment_retains_backing_without_detach_or_removal(self):
        with tempfile.TemporaryDirectory() as area:
            owner, image = self.fixture(area, uncertain=True)
            with mock.patch.object(shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(AssertionError, 'Attachment state is uncertain'):
                    DeviceStagingFilesystems._retire_images.__func__(owner)
                owner._command.assert_not_called()
                remove.assert_not_called()
                self.assertTrue(image.exists())

    def test_replaced_mount_retains_backing_without_detaching_replacement(self):
        with tempfile.TemporaryDirectory() as area:
            owner, image = self.fixture(area, mismatch=True)
            with mock.patch.object(shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(AssertionError, 'mount was replaced'):
                    DeviceStagingFilesystems._retire_images.__func__(owner)
                owner._command.assert_not_called()
                remove.assert_not_called()
                self.assertTrue(image.exists())

    def test_failed_nonforce_detach_retains_the_backing_image(self):
        with tempfile.TemporaryDirectory() as area:
            owner, image = self.fixture(area)
            owner._command.side_effect = AssertionError('generated detach busy')
            with mock.patch.object(shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(AssertionError, 'generated detach busy'):
                    DeviceStagingFilesystems._retire_images.__func__(owner)
                owner._command.assert_called_once_with('detach', str(owner.images[0]['mount']))
                remove.assert_not_called()
                self.assertTrue(image.exists())

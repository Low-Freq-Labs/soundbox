"""Native app-only packaging and preflight tests use disposable fixture bundles."""
from pathlib import Path
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from scripts import package_macos as package


class MacosInstaller(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='soundbox-installer-test-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.app = self.root / 'source/Soundbox.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        self.executable = self.app / 'Contents/MacOS/Soundbox'
        self.executable.write_bytes(b'generated fixture executable')
        self.executable.chmod(0o755)
        self.info = dict(CFBundleIdentifier=package.BUNDLE_ID, CFBundleExecutable='Soundbox',
            CFBundleShortVersionString='1.0.0', CFBundleVersion='179', SoundboxBuildProfile='personal',
            SoundboxSourceRevision='a' * 40)
        self.info['SoundboxSourceDirty'] = False
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))

    def test_installer_distribution_pins_native_platform_and_app_only_upgrade(self):
        doc = ET.fromstring(package.distribution('179'))
        self.assertEqual(doc.find('options').get('hostArchitectures'), 'arm64')
        self.assertEqual(doc.find('volume-check/allowed-os-versions/os-version').get('min'), '14.0')
        self.assertEqual(doc.find('domains').attrib,
            dict(enable_localSystem='true', enable_currentUserHome='false', enable_anywhere='false'))
        self.assertEqual(package.components(), [dict(RootRelativeBundlePath='Applications/Soundbox.app',
            BundleIsRelocatable=False, BundleIsVersionChecked=True, BundleHasStrictIdentifier=True,
            BundleOverwriteAction='upgrade')])
        self.assertNotIn('postinstall', package.PREINSTALL)
        self.assertNotIn('kill', package.PREINSTALL)
        self.assertIn('"${3:-}" != "/"', package.PREINSTALL)
        self.assertIn('not notarized', package.WELCOME)
        self.assertIn('Privacy &amp; Security', package.WELCOME)

    def test_only_internal_framework_links_are_admitted(self):
        (self.app / 'Contents/MacOS/link').symlink_to('Soundbox')
        self.assertEqual(package.manifest(self.app)['Contents/MacOS/link'], ['link', 'Soundbox'])
        (self.app / 'Contents/MacOS/link').unlink()
        (self.app / 'Contents/MacOS/link').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'escaping'):
            package.manifest(self.app)

    def test_wrong_identity_and_unsigned_inputs_are_refused(self):
        with mock.patch.object(package, 'run') as command:
            self.assertEqual(package.app_identity(self.app), self.info)
            command.assert_called_once_with(['/usr/bin/codesign', '--verify', '--deep', '--strict', self.app])
            self.info['CFBundleIdentifier'] = 'foreign.app'
            (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))
            with self.assertRaisesRegex(ValueError, 'identity'):
                package.app_identity(self.app)

    def test_distributable_installer_refuses_dirty_or_unstamped_app(self):
        with mock.patch.object(package, 'run'):
            for value in (True, None, 0):
                with self.subTest(value=value):
                    self.info['SoundboxSourceDirty'] = value
                    (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(
                        {key: item for key, item in self.info.items() if item is not None}))
                    with self.assertRaisesRegex(ValueError, 'identity'):
                        package.app_identity(self.app)
        with mock.patch.object(package, 'run', side_effect=subprocess.CalledProcessError(1, 'codesign')):
            with self.assertRaises(subprocess.CalledProcessError):
                package.app_identity(self.app)

    def test_existing_output_and_app_contained_output_are_not_overwritten(self):
        existing = self.root / package.ASSET_NAME; existing.write_bytes(b'previous package')
        with self.assertRaisesRegex(ValueError, 'fresh output'):
            package.build(self.app, existing)
        self.assertEqual(existing.read_bytes(), b'previous package')
        with self.assertRaisesRegex(ValueError, 'separate'):
            package.build(self.app, self.app / package.ASSET_NAME)

    def test_native_package_expands_to_one_app_and_keeps_recipient_files_outside(self):
        # Real pkgbuild/productbuild/expand, never Installer or /Applications.
        foreign = self.root / 'recipient/Library/Application Support/Soundbox/library.json'
        foreign.parent.mkdir(parents=True); foreign.write_bytes(b'curated fixture state')
        before = package.manifest(self.app)
        output = self.root / 'release' / package.ASSET_NAME
        native_run = package.run
        def command(args):
            if args[0] == '/usr/bin/codesign':
                return subprocess.CompletedProcess(args, 0, '', '')
            return native_run(args)
        with mock.patch.object(package, 'run', side_effect=command), mock.patch.object(package,
                'inspect_artifact', return_value={'source_sha256': 'b' * 64}), \
                mock.patch.object(package, 'source_fingerprint', return_value='b' * 64):
            receipt = package.build(self.app, output)
        self.assertEqual(package.manifest(self.app), before)
        self.assertEqual(foreign.read_bytes(), b'curated fixture state')
        self.assertEqual(receipt['package_sha256'], package.digest(output))
        self.assertEqual(receipt['payload_paths'], ['Applications/Soundbox.app'])
        self.assertFalse(receipt['recipient_data_included'])
        self.assertEqual(json.loads(output.with_suffix('.json').read_text()), receipt)
        expanded = self.root / 'independent-expanded'
        native_run(['/usr/sbin/pkgutil', '--expand-full', output, expanded])
        self.assertEqual(package.manifest(expanded / 'Soundbox-app.pkg/Payload/Applications/Soundbox.app'), before)
        scripts = expanded / 'Soundbox-app.pkg/Scripts'
        self.assertEqual(sorted(p.name for p in scripts.iterdir()), ['preinstall', 'soundbox-installer-preflight'])
        self.assertEqual((scripts / 'preinstall').read_text(), package.PREINSTALL)
        self.assertFalse(any(self.root.glob('release/soundbox-package-*')))

    def test_receipt_failure_preserves_a_replaced_published_output(self):
        output = self.root / 'release' / package.ASSET_NAME
        native_run = package.run; native_open = Path.open; native_link = os.link
        def command(args):
            if args[0] == '/usr/bin/codesign':
                return subprocess.CompletedProcess(args, 0, '', '')
            return native_run(args)
        def publish(source, destination):
            native_link(source, destination)
            destination.unlink(); destination.write_bytes(b'foreign replacement')
        def opening(path, *args, **kwargs):
            if path == output.with_suffix('.json') and args == ('x',):
                raise OSError('generated receipt failure')
            return native_open(path, *args, **kwargs)
        with mock.patch.object(package, 'run', side_effect=command), mock.patch.object(package,
                'inspect_artifact', return_value={'source_sha256': 'b' * 64}), \
                mock.patch.object(package, 'source_fingerprint', return_value='b' * 64), \
                mock.patch.object(os, 'link', side_effect=publish), mock.patch.object(Path, 'open', opening):
            with self.assertRaisesRegex(OSError, 'receipt failure'):
                package.build(self.app, output)
        self.assertEqual(output.read_bytes(), b'foreign replacement')
        self.assertFalse(output.with_suffix('.json').exists())

    def test_stale_app_fingerprint_is_refused_before_staging(self):
        output = self.root / 'release' / package.ASSET_NAME
        with mock.patch.object(package, 'app_identity', return_value=self.info), \
                mock.patch.object(package, 'inspect_artifact', return_value={'source_sha256': 'b' * 64}), \
                mock.patch.object(package, 'source_fingerprint', return_value='c' * 64), \
                mock.patch.object(package, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'fingerprint differs'):
                package.build(self.app, output)
        command.assert_not_called()
        self.assertFalse(output.parent.exists())


class NativeInstallerPreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = tempfile.TemporaryDirectory(prefix='soundbox-installer-preflight-test-')
        cls.root = Path(cls.area.name).resolve()
        cls.target = cls.root / 'target/Soundbox.app'
        cls.binary = cls.root / 'preflight'
        # A bounded owned PID list exercises real proc_pidpath on our generated
        # child; it never observes or controls the user's actual applications.
        wrapper = cls.root / 'fixture.c'
        wrapper.write_text(f'''#define SOUNDBOX_INSTALL_LOCATION {json.dumps(str(cls.target))}
#define SOUNDBOX_BUNDLE_ID "com.lowfreqlabs.soundbox.installer-fixture"
#define proc_listallpids fixture_listpids
#define main preflight_main
#include {json.dumps(str(package.PREFLIGHT_SOURCE))}
#undef main
static int fixture_pid;
int fixture_listpids(void *buffer, int bytes) {{
    if (!buffer) return 1;
    if (bytes < (int)sizeof(pid_t)) return 0;
    ((pid_t *)buffer)[0] = fixture_pid ? fixture_pid : getpid(); return 1;
}}
int main(int argc, char **argv) {{ fixture_pid = argc > 1 ? atoi(argv[1]) : 0; return preflight_main(); }}
''')
        package.run(['/usr/bin/xcrun', 'clang', '-Wall', '-Wextra', '-Werror', wrapper,
                     '-framework', 'CoreFoundation', '-o', cls.binary])

    @classmethod
    def tearDownClass(cls):
        cls.area.cleanup()

    def tearDown(self):
        if self.target.is_symlink(): self.target.unlink()
        elif self.target.exists(): shutil.rmtree(self.target)

    def fixture_bundle(self, root):
        (root / 'Contents/MacOS').mkdir(parents=True)
        (root / 'Contents/Info.plist').write_bytes(plistlib.dumps(
            {'CFBundleIdentifier': 'com.lowfreqlabs.soundbox.installer-fixture'}))
        return root

    def check(self, pid=None):
        return subprocess.run([str(self.binary), *([str(pid)] if pid else [])], capture_output=True, text=True)

    def test_missing_or_correct_existing_app_is_safe(self):
        self.assertEqual(self.check().returncode, 0)
        self.fixture_bundle(self.target)
        self.assertEqual(self.check().returncode, 0)

    def test_existing_foreign_app_or_symlink_is_refused(self):
        self.fixture_bundle(self.target)
        (self.target / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'foreign'}))
        self.assertNotEqual(self.check().returncode, 0)
        shutil.rmtree(self.target)
        other = self.root / 'other'; other.mkdir(exist_ok=True); self.target.symlink_to(other)
        self.assertNotEqual(self.check().returncode, 0)

    def test_owned_renamed_bundle_native_helper_requires_normal_quit(self):
        app = self.fixture_bundle(self.root / 'Renamed Fixture.app')
        helper = app / 'Contents/Frameworks/generated-helper'
        helper.parent.mkdir()
        source = self.root / 'generated-helper.c'
        source.write_text('#include <stdio.h>\n#include <unistd.h>\nint main(void) { puts("ready"); fflush(stdout); sleep(30); return 0; }\n')
        package.run(['/usr/bin/xcrun', 'clang', source, '-o', helper])
        process = subprocess.Popen([str(helper)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline(), 'ready\n')
            # The child's handshake follows exec; a kernel read binds the
            # executable rather than trusting the easily forged process name.
            result = self.check(process.pid)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Quit Soundbox normally', result.stderr)
            self.assertIsNone(process.poll())
        finally:
            process.terminate(); process.wait(timeout=5)
            process.stdout.close()

    def test_running_renamed_main_with_missing_info_fails_closed(self):
        app = self.fixture_bundle(self.root / 'MissingMetadata.app')
        helper = app / 'Contents/MacOS/Soundbox'
        source = self.root / 'generated-missing-info.c'
        source.write_text('#include <stdio.h>\n#include <unistd.h>\nint main(void) { puts("ready"); fflush(stdout); sleep(30); return 0; }\n')
        package.run(['/usr/bin/xcrun', 'clang', source, '-o', helper])
        process = subprocess.Popen([str(helper)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline(), 'ready\n')
            (app / 'Contents/Info.plist').unlink()
            result = self.check(process.pid)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Could not prove Soundbox', result.stderr)
            self.assertIsNone(process.poll())
        finally:
            process.terminate(); process.wait(timeout=5)
            process.stdout.close()

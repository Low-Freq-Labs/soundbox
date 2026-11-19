"""Bounded no-hardware helper boundary and actual Apple plist canonicalizer."""
from pathlib import Path
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import ipod_usb_identity as identity

GUID = '0011223344556677'
UUID = '11223344-5566-7788-99AA-BBCCDDEEFF00'


class UsbIdentityBoundary(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name).resolve()
        self.target = self.root / 'generated-device'
        self.target.mkdir()
        self.helper = self.root / 'generated-helper'
        self.helper.write_bytes(b'generated-helper-placeholder')
        self.options = dict(device_identifier='disk99s1', volume_uuid=UUID,
            usb_serial=GUID, usb_registry_id=987654, usb_product_id=0x1261)
        self.fields = dict(FireWireGUID=GUID, SerialNumber='GENERATED9ZU', VisibleBuildID='2.0.4')

    def reply(self, fields=None):
        return subprocess.CompletedProcess([], 0,
            plistlib.dumps(self.fields if fields is None else fields, fmt=plistlib.FMT_BINARY), b'')

    def test_complete_exact_reply_uses_bounded_process_and_explicit_observations(self):
        before = self.target.stat()
        with mock.patch.object(identity, '_helper_path', return_value=self.helper), \
             mock.patch.object(identity.subprocess, 'run', return_value=self.reply()) as run:
            observed = identity.read_usb_identity(self.target, **self.options)
        self.assertEqual((observed.serial_number, observed.firmware_version), ('GENERATED9ZU', '2.0.4'))
        self.assertEqual(observed.model_number, '')
        self.assertEqual(observed.serial_source, 'USB extended SysInfo')
        self.assertEqual(run.call_args.args[0], [str(self.helper), str(self.target), 'disk99s1',
            str(before.st_dev), str(before.st_ino), '987654', str(0x1261), GUID, UUID])
        self.assertEqual(run.call_args.kwargs, dict(stdin=subprocess.DEVNULL,
            capture_output=True, timeout=15, check=False))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_wrong_or_missing_embedded_guid_cannot_supply_any_display_identity(self):
        for guid in ('', '0000000000000000', 'FFEEDDCCBBAA9988', 123):
            with self.subTest(guid=guid), mock.patch.object(identity, '_helper_path', return_value=self.helper), \
                 mock.patch.object(identity.subprocess, 'run', return_value=self.reply({**self.fields, 'FireWireGUID': guid})):
                self.assertIsNone(identity.read_usb_identity(self.target, **self.options))
        with mock.patch.object(identity, '_helper_path', return_value=self.helper), \
             mock.patch.object(identity.subprocess, 'run', return_value=self.reply({**self.fields, 'FireWireGUID': '0x'+GUID})):
            self.assertEqual(identity.read_usb_identity(self.target, **self.options).serial_number, 'GENERATED9ZU')

    def test_timeout_failure_noncanonical_oversized_or_invalid_reply_is_no_claim(self):
        replies = (subprocess.CompletedProcess([], 1, self.reply().stdout, b'busy'),
                   subprocess.CompletedProcess([], 0, plistlib.dumps(self.fields), b''),
                   subprocess.CompletedProcess([], 0, b'bplist00broken', b''),
                   subprocess.CompletedProcess([], 0, b'bplist00'+bytes(1024*1024), b''),
                   self.reply([self.fields]))
        for response in replies:
            with self.subTest(size=len(response.stdout)), mock.patch.object(identity, '_helper_path', return_value=self.helper), \
                 mock.patch.object(identity.subprocess, 'run', return_value=response):
                self.assertIsNone(identity.read_usb_identity(self.target, **self.options))
        with mock.patch.object(identity, '_helper_path', return_value=self.helper), \
             mock.patch.object(identity.subprocess, 'run', side_effect=subprocess.TimeoutExpired('owned-helper', 15)):
            self.assertIsNone(identity.read_usb_identity(self.target, **self.options))

    def test_invalid_authority_never_starts_a_helper(self):
        for changes in (dict(device_identifier='disk99s1;other'), dict(volume_uuid=''),
                        dict(usb_registry_id=0), dict(usb_registry_id=True),
                        dict(usb_product_id=2**16), dict(usb_serial='0000000000000000')):
            with self.subTest(changes=changes), mock.patch.object(identity, '_helper_path', side_effect=AssertionError('No launch')):
                self.assertIsNone(identity.read_usb_identity(self.target, **{**self.options, **changes}))

    def test_same_path_remount_after_reply_does_not_publish_old_identity(self):
        def replaced(*_args, **_kwargs):
            self.target.rename(self.target.with_name('old-mount'))
            self.target.mkdir()
            return self.reply()
        with mock.patch.object(identity, '_helper_path', return_value=self.helper), \
             mock.patch.object(identity.subprocess, 'run', side_effect=replaced):
            self.assertIsNone(identity.read_usb_identity(self.target, **self.options))

    def test_packaged_path_is_bundle_local_and_absence_grants_no_fallback(self):
        bundled = self.root / 'native/soundbox-ipod-identity'
        bundled.parent.mkdir()
        bundled.write_bytes(b'generated')
        with mock.patch.object(identity.sys, 'frozen', True, create=True), \
             mock.patch.object(identity.sys, '_MEIPASS', str(self.root), create=True):
            self.assertEqual(identity._helper_path(), bundled)
            bundled.unlink()
            self.assertIsNone(identity._helper_path())
        with mock.patch.object(identity.sys, 'frozen', True, create=True), \
             mock.patch.object(identity.sys, '_MEIPASS', 'relative', create=True):
            self.assertIsNone(identity._helper_path())

    def test_import_is_qt_free_and_starts_no_device_discovery(self):
        script = 'import sys; import soundbox.ipod_usb_identity; assert not any(n.startswith("PySide6") for n in sys.modules)'
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


class NativeIdentityPlistCodec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = tempfile.TemporaryDirectory()
        root = Path(cls.area.name)
        cls.codec = root / 'generated-codec'
        native = Path(__file__).resolve().parents[1] / 'native/IpodIdentity.c'
        wrapper = root / 'codec.c'
        # Execute the exact production canonicalizer, with a separate generated
        # entrypoint that never calls the device-reader main or any USB API.
        wrapper.write_text('#define main unused_device_main\n#include '+json.dumps(str(native))+'\n#undef main\n'
            'int main(void) { unsigned char raw[MAX_BYTES+1]; size_t n=fread(raw,1,sizeof raw,stdin);\n'
            'CFDataRef out=canonical_reply(raw,n); if(!out)return 7;\n'
            'size_t len=(size_t)CFDataGetLength(out); bool ok=fwrite(CFDataGetBytePtr(out),1,len,stdout)==len;\n'
            'CFRelease(out); return ok?0:8; }\n')
        command = ['xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
            '-framework', 'IOKit', '-framework', 'CoreFoundation', '-framework', 'DiskArbitration',
            str(wrapper), '-o', str(cls.codec)]
        result = subprocess.run(command, capture_output=True, timeout=30)
        if result.returncode:
            cls.area.cleanup()
            raise AssertionError(result.stderr.decode())

    @classmethod
    def tearDownClass(cls):
        cls.area.cleanup()

    def canonicalize(self, raw):
        return subprocess.run([str(self.codec)], input=raw, capture_output=True, timeout=5)

    def test_apple_array_keys_canonicalize_without_weakening_python_parser(self):
        raw = b'''<?xml version="1.0"?><plist version="1.0"><dict>
<key>SerialNumber</key><string>GENERATED9ZU</string>
<key>VisibleBuildID</key><string>2.0.4</string>
<key>FireWireGUID</key><string>0011223344556677</string>
<key>ImageSpecifications</key><array><key>1067</key><dict><key>FormatId</key><integer>1067</integer></dict></array>
</dict></plist>'''
        with self.assertRaises(ValueError):
            plistlib.loads(raw)
        result = self.canonicalize(raw)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(result.stdout.startswith(b'bplist00'))
        parsed = plistlib.loads(result.stdout)
        self.assertEqual(parsed['SerialNumber'], 'GENERATED9ZU')
        self.assertEqual(parsed['VisibleBuildID'], '2.0.4')
        self.assertEqual(parsed['FireWireGUID'], GUID)
        self.assertEqual(parsed['ImageSpecifications'], ['1067', {'FormatId': 1067}])

    def test_valid_binary_dictionary_round_trips_all_nested_values(self):
        fields = dict(SerialNumber='GENERATED9ZU', VisibleBuildID='2.0.4',
            FireWireGUID=GUID, Unknown=dict(array=[1, 'two', b'three'], value=False))
        result = self.canonicalize(plistlib.dumps(fields, fmt=plistlib.FMT_BINARY))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(plistlib.loads(result.stdout), fields)

    def test_empty_invalid_nondictionary_and_oversized_inputs_are_refused(self):
        for raw in (b'', b'<?xml broken', plistlib.dumps(['array']), b' '*(1024*1024+1)):
            with self.subTest(size=len(raw)):
                result = self.canonicalize(raw)
                self.assertEqual(result.returncode, 7)
                self.assertEqual(result.stdout, b'')

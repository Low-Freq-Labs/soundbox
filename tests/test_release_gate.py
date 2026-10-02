"""A green release must cover the entire inventory without excused failures."""
from copy import deepcopy
import unittest
from scripts.verify_test_reports import verify


class ReleaseGate(unittest.TestCase):
    def reports(self):
        return [dict(phase=phase, inventory=['a', 'b'], selected=[name], ran=1,
                     skipped=0, expected_failures=0, passed=True)
                for phase, name in [('offscreen', 'a'), ('native', 'b')]]

    def test_complete_partition_passes(self):
        self.assertEqual(verify(self.reports()), 2)

    def test_missing_duplicate_changed_or_excused_tests_fail(self):
        mutations = [
            lambda r: r.pop(),
            lambda r: r[1].update(selected=['a']),
            lambda r: r[1].update(inventory=['a', 'c']),
            lambda r: r[1].update(selected=[], ran=0),
            lambda r: r[1].update(skipped=1),
            lambda r: r[1].update(expected_failures=1),
            lambda r: r[1].update(passed=False),
            lambda r: r[1].update(ran=0),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                reports = deepcopy(self.reports())
                mutation(reports)
                with self.assertRaises(ValueError):
                    verify(reports)


class FixtureAdmission(unittest.TestCase):
    def test_reference_missing_or_changed_bytes_fail(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        from scripts import check_fixtures
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            manifest = root / 'tests/fixtures/waveform-reference.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'files': {'frame.png': {
                'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}}}))
            with mock.patch.object(check_fixtures, 'ROOT', root), mock.patch.dict(
                    'os.environ', {'SOUNDBOX_WAVEFORM_REFERENCE': str(root)}):
                with self.assertRaisesRegex(RuntimeError, 'missing'):
                    check_fixtures.waveform_reference()
                (root / 'frame.png').write_bytes(b'abc')
                self.assertEqual(check_fixtures.waveform_reference()[0], root.resolve())
                (root / 'frame.png').write_bytes(b'abd')
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    check_fixtures.waveform_reference()

    def test_live_volume_inputs_fail_before_fixture_reads(self):
        from pathlib import Path
        from unittest import mock
        from scripts import check_fixtures
        for key, resolve in [('SOUNDBOX_IPOD_CAPTURE', check_fixtures.ipod_capture),
                             ('SOUNDBOX_WAVEFORM_REFERENCE', check_fixtures.waveform_reference)]:
            with self.subTest(key=key), mock.patch.dict('os.environ', {
                    key: '/Volumes/not-a-real-soundbox-fixture'}), mock.patch.object(
                    Path, 'is_file', side_effect=AssertionError('live fixture read')):
                with self.assertRaisesRegex(RuntimeError, 'offline'):
                    resolve()

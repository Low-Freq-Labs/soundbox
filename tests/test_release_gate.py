"""A green release must cover the entire inventory without excused failures."""
from copy import deepcopy
import unittest
from scripts.verify_test_reports import verify
from scripts.check_tests import partition


class ParallelRunner(unittest.TestCase):
    def test_real_workers_overlap_with_private_qt_state_and_a_failure_cannot_reuse_green_reports(self):
        import json
        import os
        from pathlib import Path
        import shutil
        import subprocess
        import sys
        import tempfile
        from scripts.verify_test_reports import verify_workers
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='soundbox-runner-contract-') as area:
            root=Path(area);(root/'scripts').mkdir();(root/'tests').mkdir();(root/'ready').mkdir()
            for name in ('check_tests.py','verify_test_reports.py','test-module-costs.json'):
                shutil.copyfile(source/'scripts'/name,root/'scripts'/name)
            template='''from pathlib import Path
import time,unittest
from PySide6.QtCore import QStandardPaths
class Worker(unittest.TestCase):
    def test_private_state_and_overlap(self):
        state=Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        state.mkdir(parents=True,exist_ok=True)
        with (state/'exclusive-owner').open('x') as handle:handle.write('TOKEN')
        root=Path(__file__).resolve().parents[1]
        (root/'ready/TOKEN').touch()
        deadline=time.monotonic()+10
        while len(list((root/'ready').iterdir()))<3 and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(len(list((root/'ready').iterdir())),3,'Workers did not overlap')
        self.assertEqual((state/'exclusive-owner').read_text(),'TOKEN')
        self.assertTrue(EXPECTED)
'''
            for name in ('a','b','c'):
                (root/f'tests/test_{name}.py').write_text(template.replace('TOKEN',name).replace('EXPECTED','True'))
            report=root/'report.json'
            command=[sys.executable,str(root/'scripts/check_tests.py'),'--phase','offscreen','--report',str(report)]
            environment={**os.environ,'QT_QPA_PLATFORM':'offscreen'}
            completed=subprocess.run(command,cwd=root,env=environment,capture_output=True,text=True,timeout=30)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            passed=json.loads(report.read_text());verify_workers(passed)
            self.assertTrue(passed['passed']);self.assertEqual(passed['ran'],3)
            self.assertEqual(len(passed['workers']),3)
            # Reuse exactly the same report paths. A newly failed worker must
            # replace the old green aggregate and its old per-worker report.
            target=root/'tests/test_b.py'
            target.write_text(target.read_text().replace('self.assertTrue(True)','self.assertTrue(False)'))
            completed=subprocess.run(command,cwd=root,env=environment,capture_output=True,text=True,timeout=30)
            self.assertEqual(completed.returncode,1,completed.stdout+completed.stderr)
            failed=json.loads(report.read_text())
            self.assertFalse(failed['passed']);self.assertEqual(failed['ran'],3)
            with self.assertRaises(ValueError):verify_workers(failed)


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

    def test_parallel_workers_cannot_hide_a_missing_duplicate_or_failed_partition(self):
        reports=self.reports();report=reports[0]
        report['selected']=['a','c'];report['inventory'].append('c');reports[1]['inventory'].append('c')
        report['ran']=2
        report['workers']=[dict(phase='offscreen',inventory=list(report['inventory']),selected=[name],ran=1,
            skipped=0,expected_failures=0,passed=True,exit_code=0) for name in ('a','c')]
        self.assertEqual(verify(reports),3)
        for change in (lambda w:w.pop(),lambda w:w[1].update(selected=['a']),
                       lambda w:w[0].update(exit_code=1),lambda w:w[0].update(ran=0),
                       lambda w:w[0].update(passed=False),lambda w:w[0].update(skipped=1),
                       lambda w:w[0].update(inventory=['a'])):
            sample=deepcopy(reports);change(sample[0]['workers'])
            with self.assertRaises(ValueError):verify(sample)

    def test_scheduling_weights_never_split_modules_or_omit_new_tests(self):
        ids=['a.One.first','a.One.second','b.T.first','c.T.first','d.T.new']
        for workers in (1,2,3):
            groups=partition(ids,workers,{'a':100,'b':50})
            self.assertCountEqual([identity for group in groups for identity in group],ids)
            self.assertTrue(any(group[:2]==ids[:2] for group in groups))
            for group in groups:self.assertEqual(group,sorted(group,key=ids.index))


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

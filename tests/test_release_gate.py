"""A green release must cover the entire inventory without excused failures."""
from copy import deepcopy
import unittest
from scripts.verify_test_reports import verify
from scripts.check_tests import partition


class QtThreadOwnership(unittest.TestCase):
    def test_temporary_qobject_does_not_retire_its_borrowed_application_thread(self):
        import os
        import subprocess
        from scripts.check_tests import qt_test_python_command
        code='''from PySide6.QtCore import QCoreApplication,QEvent,QObject,QThread,QStandardPaths
from shiboken6 import isValid
QStandardPaths.setTestModeEnabled(True)
app=QCoreApplication([])
owner=QObject()
thread=owner.thread()
owner.deleteLater()
QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
assert not isValid(owner)
assert isValid(thread), 'Temporary QObject retired its borrowed application thread'
assert thread is QThread.currentThread()
'''
        completed=subprocess.run(qt_test_python_command(code,platform='offscreen'),
            env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},capture_output=True,text=True,timeout=15)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)


class CocoaTestLaunchPolicy(unittest.TestCase):
    def test_owned_cocoa_probe_keeps_os_flags_and_exact_payload_arguments(self):
        import json
        import subprocess
        import sys
        from unittest import mock
        from scripts import check_tests
        code='import json,sys; print(json.dumps(sys.argv)); print("executed-once")'
        arguments=('generated path with spaces','--ordinary-user-option','YES')
        with mock.patch.object(sys,'platform','darwin'):
            command=check_tests.qt_test_python_command(code,*arguments,platform='cocoa')
        self.assertEqual(tuple(command[-6:]),check_tests._COCOA_TEST_ARGUMENTS)
        completed=subprocess.run(command,capture_output=True,text=True,check=True,timeout=10)
        self.assertEqual(completed.stdout.splitlines(),[json.dumps(['-c',*arguments]),'executed-once'])

    def test_owned_offscreen_probe_stays_unwrapped_even_on_macos(self):
        import sys
        from unittest import mock
        from scripts import check_tests
        for platform in ('offscreen','minimal'):
            with self.subTest(platform=platform),mock.patch.object(sys,'platform','darwin'):
                self.assertEqual(check_tests.qt_test_python_command('payload','argument',platform=platform),
                                 [sys.executable,'-c','payload','argument'])
        with mock.patch.object(sys,'platform','linux'):
            self.assertEqual(check_tests.qt_test_python_command('payload','argument',platform='cocoa'),
                             [sys.executable,'-c','payload','argument'])

    def test_native_launch_reexecutes_current_python_with_exact_original_cli(self):
        from pathlib import Path
        import sys
        from unittest import mock
        from scripts import check_tests
        for arguments in (['--phase','native','--report','/generated/private report.json'],
                          ['--focus','test_chosen.Check','--cocoa'],
                          ['--phase=native','--report','/generated/report.json']):
            with self.subTest(arguments=arguments):
                argv=['check_tests.py',*arguments]
                with mock.patch.object(sys,'platform','darwin'),mock.patch.object(sys,'argv',argv), \
                        mock.patch.object(check_tests.os,'execv') as execute:
                    check_tests._bootstrap_cocoa_test_process()
                execute.assert_called_once_with(sys.executable,[sys.executable,
                    str(Path(check_tests.__file__).resolve()),*arguments,*check_tests._COCOA_TEST_ARGUMENTS])
                self.assertEqual(argv,['check_tests.py',*arguments])

    def test_prepared_native_process_strips_only_internal_suffix_and_runs_once(self):
        import sys
        from unittest import mock
        from scripts import check_tests
        original=['check_tests.py','--focus','test_chosen.Check','--cocoa','--workers','1']
        argv=[*original,*check_tests._COCOA_TEST_ARGUMENTS]
        with mock.patch.object(sys,'platform','darwin'),mock.patch.object(sys,'argv',argv), \
                mock.patch.object(check_tests.os,'execv') as execute:
            check_tests._bootstrap_cocoa_test_process()
            self.assertEqual(sys.argv,original)
        execute.assert_not_called()

    def test_offscreen_planning_and_other_platforms_never_reexec(self):
        import sys
        from unittest import mock
        from scripts import check_tests
        commands=(('darwin',['--phase','offscreen','--report','/generated/report.json']),
                  ('darwin',['--focus','test_chosen.Check']),
                  ('darwin',['--suite','player','--cocoa']),
                  ('darwin',['--focus','test_chosen.Check','--cocoa','--plan']),
                  ('darwin',['--list-suites']),('darwin',['--help','--cocoa']),
                  ('linux',['--focus','test_chosen.Check','--cocoa']))
        for platform,arguments in commands:
            with self.subTest(platform=platform,arguments=arguments):
                argv=['check_tests.py',*arguments]
                with mock.patch.object(sys,'platform',platform),mock.patch.object(sys,'argv',argv), \
                        mock.patch.object(check_tests.os,'execv') as execute:
                    check_tests._bootstrap_cocoa_test_process()
                execute.assert_not_called();self.assertEqual(argv,['check_tests.py',*arguments])

    def test_unknown_user_arguments_survive_bootstrap_and_remain_parser_errors(self):
        from contextlib import redirect_stderr
        import io
        import sys
        from unittest import mock
        from scripts import check_tests
        unknown=['--focus','test_chosen.Check','--cocoa','--unrecognized-user-option']
        argv=['check_tests.py',*unknown,*check_tests._COCOA_TEST_ARGUMENTS]
        error=io.StringIO()
        with mock.patch.object(sys,'platform','darwin'),mock.patch.object(sys,'argv',argv), \
                mock.patch.object(check_tests.os,'execv') as execute:
            check_tests._bootstrap_cocoa_test_process()
            self.assertEqual(sys.argv,['check_tests.py',*unknown])
            with redirect_stderr(error),self.assertRaises(SystemExit) as failure:check_tests.main()
        self.assertEqual(failure.exception.code,2)
        self.assertIn('--unrecognized-user-option',error.getvalue())
        execute.assert_not_called()


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
            for name in ('check_tests.py','test_suites.py','verify_test_reports.py','test-module-costs.json'):
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

    def test_focused_results_cannot_be_used_as_full_release_evidence(self):
        for index in (0,1):
            reports=self.reports();reports[index]['scope']='focused'
            with self.assertRaisesRegex(ValueError,'Focused'):
                verify(reports)

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


class FocusedRunner(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        from pathlib import Path
        self.temp=tempfile.TemporaryDirectory(prefix='soundbox-focused-contract-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir();(self.root/'tests').mkdir()
        source=Path(__file__).resolve().parents[1]
        shutil.copy2(source/'scripts/check_tests.py',self.root/'scripts/check_tests.py')
        shutil.copy2(source/'scripts/test_suites.py',self.root/'scripts/test_suites.py')
        self.report=self.root/'focused.json'
        self.chosen=self.root/'tests/test_chosen.py'
        self.chosen.write_text('''from pathlib import Path
import unittest
from PySide6.QtCore import QStandardPaths
ROOT=Path(__file__).resolve().parents[1]
def setUpModule():
    with (ROOT/'module-starts').open('a') as output:output.write('start\\n')
class Check(unittest.TestCase):
    def test_one(self):
        self.assertTrue(QStandardPaths.isTestModeEnabled())
        state=Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        self.assertTrue(state.parent.name.startswith('soundbox-test-process-'))
        self.assertTrue(True)
    def test_two(self):self.assertEqual(2+2,4)
''')
        (self.root/'tests/test_unrelated.py').write_text("raise AssertionError('Unrelated test module imported')\n")

    def run_check(self,*selectors,extra=()):
        import subprocess
        import sys
        return subprocess.run([sys.executable,str(self.root/'scripts/check_tests.py'),
            '--focus',*selectors,'--report',str(self.report),*extra],
            cwd=self.root,capture_output=True,text=True,timeout=20)

    def test_only_requested_modules_import_and_overlapping_selectors_run_once(self):
        import json
        result=self.run_check('tests/test_chosen.py','test_chosen.Check.test_one')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(self.report.read_text())
        self.assertEqual(report['scope'],'focused');self.assertEqual(report['ran'],2)
        self.assertEqual(report['platform'],'offscreen')
        self.assertEqual(len(report['selected']),len(set(report['selected'])))
        self.assertEqual((self.root/'module-starts').read_text(),'start\n')
        self.assertIn('not a full release result',result.stdout)

    def test_qt_cleanup_precedes_case_release_and_does_not_retain_parentless_models(self):
        import json
        self.chosen.write_text('''import unittest,weakref
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication,QTableView,QWidget
APP=QApplication.instance() or QApplication([])
events=[]
refs={}
class AOwner(unittest.TestCase):
    def test_owned_window(self):
        self.window=QWidget()
        self.model=QStandardItemModel()
        self.view=QTableView(self.window)
        self.view.setModel(self.model)
        self.window.destroyed.connect(lambda *_:events.append('window_cpp_destroyed'))
        self.model.destroyed.connect(lambda *_:events.append('model_cpp_destroyed'))
        refs['case']=weakref.ref(self)
        refs['model']=weakref.ref(self.model)
        weakref.finalize(self,events.append,'case_released')
        self.addCleanup(self.window.deleteLater)
class BNext(unittest.TestCase):
    def test_previous_owner_has_retired(self):
        self.assertIsNone(refs['case'](),'Prior test case remains strongly referenced')
        self.assertIsNone(refs['model'](),'Prior parentless Qt model remains alive')
        self.assertEqual(events,['window_cpp_destroyed','case_released','model_cpp_destroyed'])
''')
        completed=self.run_check('test_chosen')
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        report=json.loads(self.report.read_text())
        self.assertEqual(report['ran'],2)
        self.assertEqual(report['skipped'],0)
        self.assertEqual(report['selected'],['test_chosen.AOwner.test_owned_window',
                                             'test_chosen.BNext.test_previous_owner_has_retired'])

    def test_failure_skip_expected_failure_and_bad_selector_cannot_reuse_green(self):
        import json
        initial=self.chosen.read_text()
        for replacement,field in (('self.assertTrue(False)','passed'),
                                  ("self.skipTest('not allowed')",'skipped')):
            self.chosen.write_text(initial)
            good=self.run_check('test_chosen.Check.test_one')
            self.assertEqual(good.returncode,0,good.stdout+good.stderr)
            self.chosen.write_text(initial.replace('self.assertTrue(True)',replacement))
            failed=self.run_check('test_chosen.Check.test_one')
            self.assertEqual(failed.returncode,1,failed.stdout+failed.stderr)
            report=json.loads(self.report.read_text());self.assertFalse(report['passed'])
            if field=='skipped':self.assertEqual(report[field],1)
        self.chosen.write_text(initial.replace('    def test_one(self):',
            '    @unittest.expectedFailure\n    def test_one(self):').replace('self.assertTrue(True)','self.assertTrue(False)'))
        expected=self.run_check('test_chosen.Check.test_one')
        self.assertEqual(expected.returncode,1,expected.stdout+expected.stderr)
        self.assertEqual(json.loads(self.report.read_text())['expected_failures'],1)
        invalid=self.run_check('test_chosen.Check.missing')
        self.assertNotEqual(invalid.returncode,0);self.assertFalse(self.report.exists())

    def test_cocoa_native_routing_and_full_report_protection(self):
        import json
        (self.root/'tests/test_native_deck.py').write_text('''import os,unittest
class Native(unittest.TestCase):
    def test_platform(self):self.assertEqual(os.environ['QT_QPA_PLATFORM'],'cocoa')
''')
        refused=self.run_check('test_native_deck')
        self.assertNotEqual(refused.returncode,0);self.assertFalse(self.report.exists())
        cocoa=self.run_check('test_native_deck',extra=('--cocoa',))
        self.assertEqual(cocoa.returncode,0,cocoa.stdout+cocoa.stderr)
        self.assertEqual(json.loads(self.report.read_text())['platform'],'cocoa')
        self.report=self.root/'build/check/offscreen.json';self.report.parent.mkdir(parents=True)
        self.report.write_text('preserved release report')
        protected=self.run_check('test_chosen')
        self.assertNotEqual(protected.returncode,0)
        self.assertEqual(self.report.read_text(),'preserved release report')

    def suite_fixture(self):
        (self.root/'tests/test_native_deck.py').write_text('''import os,unittest
class Native(unittest.TestCase):
    def test_platform(self):self.assertEqual(os.environ['QT_QPA_PLATFORM'],'cocoa')
''')
        suites={
            'library':dict(description='Library',quick=['test_chosen.Check.test_one'],
                           integration=['test_chosen.Check.test_two'],recovery=[]),
            'overlap':dict(description='Shared case',quick=['test_chosen.Check.test_one'],integration=[],recovery=[]),
            'player':dict(description='Player',quick=['test_native_deck.Native.test_platform'],integration=[],recovery=[])}
        (self.root/'scripts/test-suites.json').write_text(__import__('json').dumps(dict(version=1,suites=suites)))

    def run_suites(self,*arguments):
        import subprocess
        import sys
        return subprocess.run([sys.executable,str(self.root/'scripts/check_tests.py'),*arguments],
                              cwd=self.root,capture_output=True,text=True,timeout=20)

    def test_named_preview_imports_no_qt_and_combined_phases_execute_each_case_once(self):
        import json
        self.suite_fixture()
        blocker=self.root/'PySide6';blocker.mkdir()
        (blocker/'__init__.py').write_text("raise AssertionError('Preview imported Qt')\n")
        for arguments in (('--list-suites',),('--suite','library','player','--plan'),
                          ('--focus','test_chosen.Check.test_one','--plan')):
            preview=self.run_suites(*arguments)
            self.assertEqual(preview.returncode,0,preview.stdout+preview.stderr)
        self.assertFalse((self.root/'module-starts').exists())
        (blocker/'__init__.py').unlink()
        # Remove only the generated blocker; Python did not import it, so there
        # is no bytecode or native state to retire.
        blocker.rmdir()
        result=self.run_suites('--suite','library','overlap','player','--level','integration',
                               '--focus','test_chosen.Check.test_one','--report',str(self.report))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(self.report.read_text())
        self.assertEqual(report['scope'],'focused');self.assertTrue(report['passed'])
        self.assertEqual(report['ran'],3);self.assertEqual(len(set(report['selected'])),3)
        self.assertEqual([phase['phase'] for phase in report['phases']],['offscreen','native'])
        self.assertEqual((self.root/'module-starts').read_text(),'start\n')
        with self.assertRaisesRegex(ValueError,'Focused'):verify([report])

    def test_named_failure_missing_phase_and_bad_catalog_clear_old_success(self):
        import json
        self.suite_fixture()
        arguments=('--suite','library','player','--report',str(self.report))
        passed=self.run_suites(*arguments)
        self.assertEqual(passed.returncode,0,passed.stdout+passed.stderr)
        native=self.root/'tests/test_native_deck.py';original=native.read_text()
        native.write_text(original.replace("self.assertEqual(os.environ['QT_QPA_PLATFORM'],'cocoa')",'self.assertTrue(False)'))
        failed=self.run_suites(*arguments)
        self.assertEqual(failed.returncode,1,failed.stdout+failed.stderr)
        self.assertFalse(json.loads(self.report.read_text())['passed'])
        native.write_text('raise SystemExit(3)\n'+original)
        missing=self.run_suites(*arguments)
        self.assertEqual(missing.returncode,1,missing.stdout+missing.stderr)
        report=json.loads(self.report.read_text());self.assertFalse(report['passed'])
        self.assertIn('error',report['phases'][1])
        phases=self.report.parent/(self.report.stem+'-phases')
        self.assertFalse((phases/'native.json').exists())
        path=self.root/'scripts/test-suites.json';catalog=json.loads(path.read_text())
        catalog['suites']['library']['quick']=['test_chosen.Check.missing'];path.write_text(json.dumps(catalog))
        invalid=self.run_suites(*arguments)
        self.assertNotEqual(invalid.returncode,0)
        self.assertFalse(self.report.exists());self.assertFalse((phases/'offscreen.json').exists())

    def test_named_cocoa_override_and_release_report_protection(self):
        import json
        self.suite_fixture()
        result=self.run_suites('--suite','library','player','--cocoa','--report',str(self.report))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(self.report.read_text())
        self.assertEqual([phase['phase'] for phase in report['phases']],['native'])
        self.assertEqual(report['planned_phases']['offscreen'],[])
        self.assertEqual(report['ran'],2)
        self.report=self.root/'build/check/native.json';self.report.parent.mkdir(parents=True)
        self.report.write_text('preserved full gate')
        refused=self.run_suites('--suite','library','--report',str(self.report))
        self.assertNotEqual(refused.returncode,0)
        self.assertEqual(self.report.read_text(),'preserved full gate')


class SchedulingMeasurements(unittest.TestCase):
    def test_only_complete_passing_measurements_refresh_hints(self):
        from scripts.update_test_costs import measured_costs
        ids=['a.T.one','a.T.two','b.T.one']
        report=dict(scope='full',phase='offscreen',inventory=ids,selected=ids,ran=3,
                    skipped=0,expected_failures=0,passed=True)
        report['workers']=[{**report,'selected':selected,'ran':len(selected),'exit_code':0}
                           for selected in (ids[:2],ids[2:])]
        report['case_timings']=[dict(id=identity,seconds=seconds)
                                for identity,seconds in zip(ids,(2.0,1.0,.5))]
        self.assertEqual(measured_costs(report),{'a':3.0,'b':.5})
        for change in (lambda r:r.update(scope='focused'),lambda r:r.update(passed=False),
                       lambda r:r['case_timings'].pop(),
                       lambda r:r['case_timings'].append(r['case_timings'][0]),
                       lambda r:r['case_timings'][0].update(seconds=float('nan')),
                       lambda r:r['case_timings'][0].update(seconds=-1)):
            sample=deepcopy(report);change(sample)
            with self.assertRaises(ValueError):measured_costs(sample)

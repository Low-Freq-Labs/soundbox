"""Actual worker phase delivery advances stock progress without inventing completion."""
from pathlib import Path
import tempfile
import threading
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton
from shiboken6 import isValid

from desktop.app import MainWindow
from desktop.device_progress import stock_phase_progress
from desktop.devices import human
from desktop.workers import OneShotTask
from soundbox.device_operations import DeviceOperationCoordinator
from soundbox.operation_control import phase_progress


class StockProgressUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.area).resolve()
        self.progress = QProgressBar()
        self.status = QLabel()
        self.cancel = QPushButton()
        self.workers = []
        self.host = SimpleNamespace(
            _device_coordinator=DeviceOperationCoordinator(),
            _device_mount_tokens={str(self.root): self.root.stat().st_dev},
            _stock_plan_worker=None, _stock_commit_worker=None,
            _shutting_down=False, _cancelling=False,
            progress=self.progress, status_label=self.status, cancel_btn=self.cancel,
            _start_pulse=mock.Mock(), _set_status_count=mock.Mock(),
            _update_device_action_state=mock.Mock())
        self.host._set_device_progress = MethodType(MainWindow._set_device_progress, self.host)
        self.host._on_device_phase_progress = MethodType(MainWindow._on_device_phase_progress, self.host)
        self.addCleanup(self.close)

    def close(self):
        for worker, release in self.workers:
            release.set()
            if isValid(worker): worker.wait()
        self.app.processEvents()
        self.progress.deleteLater(); self.status.deleteLater(); self.cancel.deleteLater()
        QCoreApplication.sendPostedEvents()

    def until(self, condition,*,timeout=3):
        limit = time.monotonic() + timeout
        while not condition() and time.monotonic() < limit:
            self.app.processEvents(); QTest.qWait(1)
        self.assertTrue(condition(), 'Generated progress did not arrive')

    def start(self, work, *, stock=True,owner_name=None,intent='commit'):
        release = threading.Event()
        worker = OneShotTask(lambda: work(release))
        self.workers.append((worker, release))
        name = owner_name or ('_stock_commit_worker' if stock else '_dual_commit_worker')
        setattr(self.host, name, worker)
        worker.finished.connect(lambda: MainWindow._finish_worker(self.host, name, worker))
        MainWindow._start_device_worker(self.host, worker, str(self.root), intent)
        return worker, release

    def test_verified_byte_phases_advance_large_export_but_never_claim_final_readback(self):
        total = 4_500_000_000
        rows = [('Checking iPod files', 1, 2), ('Checking iPod files', 2, 2),
                ('iTunes copying', total // 2, 2 * total), ('iTunes copying', total, 2 * total),
                ('iTunes verifying', total + total // 2, 2 * total),
                ('iTunes verifying', 2 * total, 2 * total),
                ('iTunes media', total, total), ('iTunes artwork', 10, 20),
                ('iTunes artwork', 20, 20), ('iTunes database', 1, 1),
                ('iTunes cleanup', 1, 1)]
        seen = []
        original = self.host._on_device_phase_progress
        def observed(worker, stage, done, count, label):
            original(worker, stage, done, count, label)
            seen.append((stage, done, self.progress.value()))
        self.host._on_device_phase_progress = observed
        self.host._set_device_progress(150)
        def work(release):
            for stage, done, count in rows: phase_progress(stage, done, count, 'Generated song')
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work)
        self.until(lambda: len(seen) == len(rows))
        values = [value for _, _, value in seen]
        self.assertEqual(values, sorted(values))
        self.assertGreater(len(set(values)), 5)
        self.assertEqual(seen[2], ('iTunes copying', total // 2, 350))
        self.assertEqual(seen[3], ('iTunes copying', total, 450))
        self.assertEqual(seen[4], ('iTunes verifying', total + total // 2, 550))
        self.assertEqual(seen[5], ('iTunes verifying', 2 * total, 650))
        self.assertEqual(values[-1], 800)
        self.assertNotIn(1000, values)
        self.assertIn('Finishing iPod', self.host._start_pulse.call_args.args)
        release.set(); self.until(lambda: self.host._stock_commit_worker is None)
        self.assertIsNone(self.host._device_coordinator.active)

    def test_cancelled_and_foreign_worker_events_cannot_advance_or_replace_status(self):
        entered = threading.Event()
        def work(release):
            entered.set()
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work)
        self.until(entered.is_set)
        self.host._set_device_progress(377)
        self.host._device_coordinator.active.cancellation.set()
        self.host._on_device_phase_progress(worker, 'iTunes media', 100, 100, 'late')
        self.host._on_device_phase_progress(object(), 'iTunes database', 1, 1, 'foreign')
        self.host._on_device_phase_progress(worker, 'Acquiring source', 50, 100, 'late.wav')
        self.host._on_device_phase_progress(object(), 'Acquiring source', 50, 100, 'foreign.wav')
        self.host._on_device_phase_progress(worker, 'Waiting for cloud source', 1, 2, 'late.wav')
        self.host._on_device_phase_progress(object(), 'Waiting for cloud source', 1, 2, 'foreign.wav')
        self.assertEqual(self.progress.value(), 377)
        self.host._start_pulse.assert_not_called()
        release.set(); self.until(lambda: self.host._stock_commit_worker is None)

    def test_dual_primary_progress_keeps_its_existing_owner(self):
        entered = threading.Event()
        def work(release):
            entered.set()
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work, stock=False)
        self.until(entered.is_set)
        self.host._set_device_progress(377)
        self.host._on_device_phase_progress(worker, 'iTunes media', 100, 100, 'nested stock copy')
        self.assertEqual(self.progress.value(), 377)
        self.host._start_pulse.assert_not_called()
        release.set(); self.until(lambda: self.host._dual_commit_worker is None)

    def real_footer(self):
        host=self.host
        host._cancel_export=mock.Mock()
        footer=MainWindow._bottom_bar(host)
        self.addCleanup(footer.deleteLater)
        # Production footer controls exercise the actual layout and elision.
        self.progress.deleteLater();self.status.deleteLater();self.cancel.deleteLater()
        self.progress=host.progress;self.status=host.status_label;self.cancel=host.cancel_btn
        host._stock_view_warming=False;host._pulse_word=None;host._status_generation=0
        host._pulse_timer=QTimer();host._pulse_timer.setInterval(500)
        self.addCleanup(host._pulse_timer.stop)
        host._pulse_timer.timeout.connect(lambda:MainWindow._pulse_tick(host))
        host._set_status_count=MethodType(MainWindow._set_status_count,host)
        host._start_pulse=MethodType(MainWindow._start_pulse,host)
        host._set_device_progress(110)
        footer.resize(640,28);footer.show();self.app.processEvents()
        return footer

    def test_source_bytes_are_visible_in_the_real_narrow_footer_without_advancing_overall_work(self):
        host=self.host;footer=self.real_footer()
        next_bytes=threading.Event();next_phase=threading.Event()
        self.addCleanup(next_bytes.set);self.addCleanup(next_phase.set)
        name='Generated '+('long current source filename '*12)+'.wav'
        total=512*1024*1024
        def work(release):
            phase_progress('Acquiring source',128*1024*1024,total,name)
            if not next_bytes.wait(3):raise RuntimeError('Generated byte update was not released')
            phase_progress('Acquiring source',256*1024*1024,total,name)
            if not next_phase.wait(3):raise RuntimeError('Generated phase update was not released')
            phase_progress('iTunes planning',2,10,'Source ready')
            if not release.wait(3):raise RuntimeError('Generated footer test was not released')
        _worker,release=self.start(work)
        first=f'{human(128*1024*1024)} / {human(total)}'
        second=f'{human(256*1024*1024)} / {human(total)}'
        self.until(lambda:host.status_count_label.text()==first)
        self.assertEqual(host.progress.value(),110)
        self.assertIn(name,host.status_label.text())
        self.assertFalse(host.status_count_label.isHidden())
        self.assertFalse(host.cancel_btn.isHidden())
        self.assertLess(host.status_label.width(),host.status_label.fontMetrics().horizontalAdvance(host.status_label.text()))
        self.assertEqual(footer.width(),640)
        controls=(host.progress.geometry(),host.cancel_btn.geometry(),host.status_count_label.geometry())
        self.assertLessEqual(host.cancel_btn.geometry().right(),host.status_count_label.geometry().left())
        self.assertLessEqual(host.status_count_label.geometry().right(),host.status_label.geometry().left())
        next_bytes.set();self.until(lambda:host.status_count_label.text()==second)
        self.assertEqual(host.progress.value(),110)
        self.assertEqual((host.progress.geometry(),host.cancel_btn.geometry(),host.status_count_label.geometry()),controls)
        next_phase.set();self.until(lambda:host._pulse_word=='Preparing iPod')
        self.assertTrue(host.status_count_label.isHidden())
        self.assertNotIn(name,host.status_label.text())
        self.assertEqual(host.progress.value(),110)
        release.set();self.until(lambda:host._stock_commit_worker is None)
        footer.hide()

    def test_source_byte_details_never_claim_transaction_or_verification_completion(self):
        progress=stock_phase_progress('Acquiring source',200,100)
        self.assertEqual((progress.message,progress.value,progress.unit),('Preparing source',None,'bytes'))
        waiting=stock_phase_progress('Waiting for cloud source',1,2)
        self.assertEqual((waiting.message,waiting.value,waiting.unit),('Waiting for cloud source',None,'retries'))
        for done,total in ((-1,100),(1,-1),(True,100),(1,'100')):
            with self.subTest(done=done,total=total):
                self.assertIsNone(stock_phase_progress('Acquiring source',done,total))
        counter=QLabel();self.addCleanup(counter.deleteLater)
        host=SimpleNamespace(status_count_label=counter)
        MainWindow._set_status_count(host,200,100,byte_counts=True)
        self.assertEqual(counter.text(),'200.0 B / 100.0 B')
        MainWindow._set_status_count(host,200,100)
        self.assertEqual(counter.text(),'100/100')

    def test_catalog_probe_keeps_overall_work_and_source_detail_while_normal_plan_advances(self):
        host=self.host;footer=self.real_footer()
        for intent,initial in (('plan-catalog-probe',0),('plan-catalog-probe',110),('plan',0)):
            with self.subTest(intent=intent,initial=initial):
                host._set_device_progress(initial)
                host._start_pulse('Checking playlist changes')
                before_source=threading.Event();after_source=threading.Event()
                self.addCleanup(before_source.set);self.addCleanup(after_source.set)
                seen=[]
                original=host._on_device_phase_progress
                def observed(worker,stage,done,total,label):
                    original(worker,stage,done,total,label)
                    seen.append((stage,done,host.progress.value(),host._pulse_word,
                        host.status_count_label.isHidden(),host.status_label.toolTip()))
                host._on_device_phase_progress=observed
                def work(release):
                    for done in range(1,5):phase_progress('iTunes planning',done,4,'Generated playlist')
                    if not before_source.wait(3):raise RuntimeError('Generated probe source not released')
                    phase_progress('Waiting for cloud source',1,2,'Current source.wav')
                    phase_progress('Acquiring source',50,100,'Current source.wav')
                    if not after_source.wait(3):raise RuntimeError('Generated probe finish not released')
                    if not release.wait(3):raise RuntimeError('Generated probe worker not released')
                _worker,release=self.start(work,owner_name='_stock_plan_worker',intent=intent)
                self.until(lambda:len(seen)==4)
                if intent=='plan-catalog-probe':
                    self.assertEqual([row[2] for row in seen],[initial]*4)
                    self.assertTrue(all(row[3]=='Checking playlist changes' and row[4] for row in seen))
                else:
                    self.assertEqual([row[2] for row in seen],[37,75,112,150])
                    self.assertEqual(host._pulse_word,'Preparing iPod')
                self.assertIn('4 / 4 checks',host.status_label.toolTip())
                before_source.set();self.until(lambda:len(seen)==6)
                held=initial if intent=='plan-catalog-probe' else 150
                self.assertEqual(seen[4][2:4],(held,'Waiting for cloud source: Current source.wav'))
                self.assertEqual(host._pulse_word,'Preparing source: Current source.wav')
                self.assertEqual(host.status_count_label.text(),'50.0 B / 100.0 B')
                self.assertEqual(host.progress.value(),held)
                after_source.set();release.set()
                self.until(lambda:host._stock_plan_worker is None)
                host._on_device_phase_progress=original
        footer.hide()

    def test_generic_device_primary_progress_is_not_replaced_by_ipod_source_detail(self):
        entered=threading.Event()
        def work(release):
            entered.set()
            if not release.wait(3):raise RuntimeError('Generated generic worker was not released')
        worker,release=self.start(work,owner_name='_cdj_worker')
        self.until(entered.is_set)
        self.host._set_device_progress(110)
        self.host._on_device_phase_progress(worker,'Acquiring source',50,100,'Generic source.wav')
        self.host._on_device_phase_progress(worker,'Waiting for cloud source',1,2,'Generic source.wav')
        self.host._start_pulse.assert_not_called()
        self.host._set_status_count.assert_not_called()
        self.assertEqual(self.progress.value(),110)
        release.set();self.until(lambda:self.host._cdj_worker is None)

    def test_real_cloud_wait_shows_filename_retry_then_source_bytes_without_false_overall_work(self):
        from soundbox import source_read_retry
        from soundbox.source_acquisition import acquire_source,SourceContract
        host=self.host;footer=self.real_footer()
        source=self.root/'Generated cloud waiting.wav';payload=b'Generated local source snapshot'
        source.write_bytes(payload)
        finish_capture=threading.Event();self.addCleanup(finish_capture.set)
        def hold_capture(_done):
            if not finish_capture.wait(3):raise RuntimeError('Generated source capture was not released')
        def work(release):
            retry=source_read_retry.ProviderReadRetry()
            if not retry.wait_after(OSError(81,'Generated provider readiness'),source):
                raise RuntimeError('Generated readiness retry was refused')
            with acquire_source(SourceContract(source),self.root/'staging',on_bytes=hold_capture) as captured:
                if captured.path.read_bytes()!=payload:raise RuntimeError('Generated capture changed bytes')
            phase_progress('iTunes planning',2,10,'Source ready')
            if not release.wait(3):raise RuntimeError('Generated cloud UI worker was not released')
        with mock.patch.object(source_read_retry,'is_cloud_storage_source',return_value=True):
            _worker,release=self.start(work)
            self.until(lambda:host._pulse_word=='Waiting for cloud source: '+source.name)
            self.assertEqual(host.status_count_label.text(),'1/2')
            self.assertIn(source.name,host.status_label.text())
            self.assertEqual(host.progress.value(),110)
            self.assertIn('1 / 2 retries',host.status_label.toolTip())
            self.until(lambda:host._pulse_word=='Preparing source: '+source.name,timeout=5)
            self.assertEqual(host.status_count_label.text(),f'{human(0)} / {human(len(payload))}')
            self.assertEqual(host.progress.value(),110)
            finish_capture.set();self.until(lambda:host._pulse_word=='Preparing iPod')
            self.assertTrue(host.status_count_label.isHidden())
            self.assertEqual(host.progress.value(),110)
            release.set();self.until(lambda:host._stock_commit_worker is None)
        self.assertEqual(source.read_bytes(),payload)
        self.assertFalse(list((self.root/'staging').iterdir()))
        footer.hide()

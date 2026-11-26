"""Accepted empty USB setup keeps device/source ownership through both workers."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.devices import DeviceFacts
from soundbox.device_operations import DeviceSessionIdentity
from tests.test_cdj_delete_ui import Host


class CdjInitializeUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.facts=DeviceFacts(volume_uuid='generated-volume',writable=True)
        self.facts_mock=self.enterContext(mock.patch('desktop.cdj.device_facts',return_value=self.facts))
        self.plan=SimpleNamespace(changes=[object()],requires_commit=True,overwritten_playlists=(),close=mock.Mock())
        self.prepare=self.enterContext(mock.patch('soundbox.cdj_export.plan_initialize',return_value=self.plan))
        self.commit=self.enterContext(mock.patch('soundbox.cdj_transaction.commit',return_value={'done':True}))
        self.warning=self.enterContext(mock.patch('desktop.cdj.QMessageBox.warning'))
        self.workers=[];self.addCleanup(lambda:[w.deleteLater() for w in self.workers])

    def host(self,role='generic_usb'):
        host=Host(self.root,role);host.settings={'cdj_my_settings':{'PLAYER':{'fixture':1}}}
        host._device_coordinator=SimpleNamespace(mounts={host.dev:DeviceSessionIdentity(host.dev,7)})
        host._state_repository=SimpleNamespace(generation=1,area=SimpleNamespace(generation=1))
        proof=SimpleNamespace(state='missing',target=Path(host.dev),volume_uuid='generated-volume',
                              target_role=role,mount_identity=(7,42))
        return host,proof

    def deliver(self,host):
        worker=host.started[-1][0];self.workers.append(worker);worker.run();worker.finished.emit()
        return worker

    def test_both_roles_configure_only_empty_native_assets_without_source_capture_or_format(self):
        for role in ('generic_usb','ipod_usb'):
            with self.subTest(role=role),mock.patch('soundbox.cdj_source.capture_metadata',side_effect=AssertionError('source capture')),\
                    mock.patch('soundbox.usb_format.capture',side_effect=AssertionError('format')):
                host,proof=self.host(role);self.prepare.reset_mock();self.commit.reset_mock()
                host.tag_controller=SimpleNamespace(owned=True,pending=[{'error':'Unrelated queued tag'}])
                host._begin_cdj_initialize(host.dev,target_role=role,expected_setup=proof)
                self.assertEqual(host.started[-1][2],'plan-cdj-initialize')
                self.prepare.assert_not_called();self.commit.assert_not_called()
                host.settings['cdj_my_settings']['PLAYER']['fixture']=2
                self.deliver(host)
                options=self.prepare.call_args.kwargs
                self.assertEqual((options['token'],options['volume_uuid'],options['target_role']),
                                 (7,'generated-volume',role))
                self.assertIs(options['expected_setup'],proof)
                self.assertEqual(options['profile'],{'PLAYER':{'fixture':1}})
                self.assertIn(host.lib.source,options['sources'])
                self.assertEqual(options['name'],'device')
                self.assertEqual(host.started[-1][2],'commit-cdj-initialize')
                self.commit.assert_not_called();self.deliver(host)
                self.commit.assert_called_once_with(self.plan,should_cancel=mock.ANY,on_progress=mock.ANY)
                self.assertEqual(host._flash_status.call_args.args[0],'USB configured for CDJ export ✓')
                self.assertNotIn('Sync',host._flash_status.call_args.args[0])

    def test_stale_or_nonmissing_confirmation_never_starts_a_worker(self):
        for field,value in [('state','configured'),('target',Path('/other')),('volume_uuid','other'),
                            ('target_role','ipod_usb'),('mount_identity',(8,42))]:
            with self.subTest(field=field):
                host,proof=self.host();setattr(proof,field,value)
                host._begin_cdj_initialize(host.dev,expected_setup=proof)
                self.assertEqual(host.started,[])
        self.prepare.assert_not_called();self.commit.assert_not_called()

    def test_unwritable_or_busy_target_never_releases_or_starts_device_work(self):
        for busy in (False,True):
            with self.subTest(busy=busy):
                host,proof=self.host();host._device_operation_owned.return_value=busy
                self.facts_mock.return_value=replace(self.facts,writable=busy)
                host._begin_cdj_initialize(host.dev,expected_setup=proof)
                self.assertEqual(host.started,[]);host._release_eject_ready.assert_not_called()
        self.facts_mock.return_value=self.facts

    def test_discovery_deferral_keeps_exact_role_and_proof_and_refuses_a_remount(self):
        for remount in (False,True):
            with self.subTest(remount=remount):
                host,proof=self.host('ipod_usb');callbacks=[]
                host._defer_device_operation_until_scan_finishes.side_effect=lambda callback,**_:callbacks.append(callback) or True
                host._begin_cdj_initialize(host.dev,target_role='ipod_usb',expected_setup=proof)
                self.assertEqual(host.started,[])
                if remount:host._device_mount_tokens[host.dev]=8
                host._defer_device_operation_until_scan_finishes.side_effect=None
                host._defer_device_operation_until_scan_finishes.return_value=False;callbacks.pop()()
                self.assertEqual(bool(host.started),not remount)
                if not remount:
                    self.deliver(host);self.assertIs(self.prepare.call_args.kwargs['expected_setup'],proof)
                    host.started[-1][0].cancel();self.deliver(host)

    def test_source_restore_and_mount_changes_before_planning_refuse(self):
        for change in ('source','restore','mount','uuid','profile'):
            with self.subTest(change=change):
                host,proof=self.host();self.prepare.reset_mock()
                host._begin_cdj_initialize(host.dev,expected_setup=proof)
                if change=='source':host.lib.source_library_id='other'
                elif change=='restore':host._state_repository.area.generation+=1
                elif change=='mount':host._device_coordinator.mounts[host.dev]=DeviceSessionIdentity(host.dev,8)
                elif change=='uuid':self.facts_mock.return_value=replace(self.facts,volume_uuid='other')
                elif change=='profile':host._device_profiles.clear()
                self.deliver(host);self.prepare.assert_not_called();self.commit.assert_not_called()
                self.facts_mock.return_value=self.facts

    def test_an_existing_batch_cannot_be_hijacked_by_setup(self):
        host,proof=self.host();host._export_batch={'pending':[]}
        host._begin_cdj_initialize(host.dev,expected_setup=proof)
        self.assertEqual(host.started,[]);self.prepare.assert_not_called()

    def test_cancelled_planning_never_initializes_or_commits(self):
        host,proof=self.host();host._begin_cdj_initialize(host.dev,expected_setup=proof)
        host.started[-1][0].cancel();self.deliver(host)
        self.prepare.assert_not_called();self.commit.assert_not_called()
        self.assertEqual(host._flash_status.call_args.args[0],'USB configuration cancelled')

    def test_late_result_is_closed_when_mount_changes_before_plan_delivery(self):
        host,proof=self.host();host._begin_cdj_initialize(host.dev,expected_setup=proof)
        worker=host.started[-1][0];self.workers.append(worker);worker.run()
        host._device_mount_tokens[host.dev]=8;worker.finished.emit()
        self.plan.close.assert_called_once();self.commit.assert_not_called()
        self.assertEqual(len(host.started),1)

    def test_source_change_before_commit_never_publishes_and_closes_plan(self):
        host,proof=self.host();host._begin_cdj_initialize(host.dev,expected_setup=proof);self.deliver(host)
        host.lib.source=Path('/different/master.db');self.deliver(host)
        self.commit.assert_not_called();self.plan.close.assert_called_once()

    def test_navigation_and_ordinary_settings_save_do_not_cancel_accepted_configuration(self):
        host,proof=self.host('ipod_usb')
        host._begin_cdj_initialize(host.dev,target_role='ipod_usb',expected_setup=proof)
        host._state_repository.generation+=1;host._current_device_role=''
        self.deliver(host);self.deliver(host)
        self.commit.assert_called_once();host._present_usb_device.assert_not_called()
        self.assertEqual(host._flash_status.call_args.args[0],'USB configured for CDJ export ✓')

    def test_new_native_catalog_or_other_plan_failure_is_not_reported_as_configuration_success(self):
        host,proof=self.host();self.prepare.side_effect=ValueError('The USB already has a native library')
        host._begin_cdj_initialize(host.dev,expected_setup=proof);self.deliver(host)
        self.commit.assert_not_called();self.warning.assert_called_once()
        self.assertEqual(host._flash_status.call_args.args[0],'CDJ USB operation stopped')

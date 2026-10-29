"""USB drag intent and owned worker handoff, without devices or app state."""
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.cdj import CdjDeviceMixin


class CdjDropIntent(unittest.TestCase):
    def host(self):
        started = []
        host = SimpleNamespace(_shutting_down=False,_cdj_plan=None,_cdj_worker=None,
            _set_device_progress=mock.Mock(),_start_pulse=mock.Mock(),_finish_cdj=mock.Mock(),
            _start_device_worker=lambda w,dev,intent:started.append((w,dev,intent)))
        def finished(attr,worker,callback):
            self.assertIs(getattr(host,attr),worker)
            setattr(host,attr,None)
            callback(worker.result)
        host._finish_device_result_worker=finished
        return host,started

    def test_drop_publishes_without_a_second_dialog_and_keeps_plan_until_finished(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[object()],close=mock.Mock(),replaces_native_library=True)
        with mock.patch('desktop.cdj.QMessageBox',side_effect=AssertionError('Unexpected approval')), \
             mock.patch('desktop.cdj.cdj_transaction.commit',return_value={'generation':'new'}) as commit:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan,from_drop=True)
            worker,dev,intent=started[0]
            self.assertEqual((dev,intent),('/not-a-device','commit-cdj'))
            commit.assert_not_called();plan.close.assert_not_called();host._finish_cdj.assert_not_called()
            worker.run()
            commit.assert_called_once();plan.close.assert_called_once()
            host._finish_cdj.assert_not_called()
            worker.finished.emit()
            host._finish_cdj.assert_called_once_with('/not-a-device',{'generation':'new'})

    def test_cancelled_drop_closes_preparation_without_publishing(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[object()],close=mock.Mock())
        with mock.patch('desktop.cdj.cdj_transaction.commit') as commit:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan,cancelled=True,from_drop=True)
        self.assertEqual(started,[]);commit.assert_not_called();plan.close.assert_called_once()
        self.assertIsInstance(host._finish_cdj.call_args.args[1],RuntimeError)

    def test_drop_authorization_survives_discovery_deferral(self):
        pending=[]
        host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda callback:pending.append(callback) or True)
        playlists=[object()]
        CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',playlists,from_drop=True,drop_target=(7,'fixture'))
        pending[0]()
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,settings=None,from_drop=True,drop_target=(7,'fixture'))

    def test_generic_playlist_drop_carries_its_own_authorization(self):
        from desktop.app import MainWindow
        playlists=[object()]
        host=SimpleNamespace(lib=object(),_resolve_jobs=lambda _:playlists,
            _device_profiles={'/not-a-device':SimpleNamespace(kind='generic_usb')},_begin_cdj_export=mock.Mock())
        MainWindow._on_device_drop(host,'/not-a-device',[('cust','Set')])
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,from_drop=True)

    def test_drop_authorization_survives_waiting_for_source_edits(self):
        pending=[]
        host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda _:False,
            _device_operation_owned=lambda:False,tag_controller=SimpleNamespace(owned=True),
            _start_pulse=mock.Mock(),cancel_btn=mock.Mock())
        playlists=[object()]
        with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:pending.append(callback)):
            CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',playlists,from_drop=True,drop_target=(7,'fixture'))
        host.tag_controller.owned=False
        pending[0]()
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,settings=None,from_drop=True,drop_target=(7,'fixture'))

    def test_settings_and_update_actions_publish_without_a_second_review(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[SimpleNamespace(new_sha256='hash',size=1,path='catalog')],close=mock.Mock())
        with mock.patch('desktop.cdj.QMessageBox') as dialog:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan)
            dialog.assert_not_called()
        self.assertEqual(len(started),1)
        plan.close.assert_not_called()

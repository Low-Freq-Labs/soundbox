"""Collection hide binds the reviewed song and acknowledges actual durability."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.app import MainWindow
from soundbox.library import Library,Track


class CollectionHideFreshness(unittest.TestCase):
    def host(self):
        track=Track('one','Song','Artist','file:///synthetic/one.wav',source_native_id='1',source_uuid='uuid-one')
        return SimpleNamespace(lib=Library({'one':track},[], '/synthetic/master.db',
            source_kind='rekordbox-db',source_library_id='library'),
            hidden={'older'},hidden_dates={'older':'existing date'},
            _save_state=mock.Mock(return_value=True),_after_track_edit=mock.Mock(),
            _discard_graveyard_analysis=mock.Mock(),_flash_status=mock.Mock())

    def test_same_id_replacement_source_or_lifecycle_change_during_dialog_is_not_hidden(self):
        from desktop.app import QMessageBox
        for change in ('uuid','native-id','path','source','library','kind','maintenance','shutdown'):
            with self.subTest(change=change):
                host=self.host()
                def confirm(*_args):
                    track=host.lib.tracks_by_id['one']
                    if change=='uuid':host.lib.tracks_by_id['one']=replace(track,source_uuid='replacement')
                    elif change=='native-id':host.lib.tracks_by_id['one']=replace(track,source_native_id='2')
                    elif change=='path':host.lib.tracks_by_id['one']=replace(track,location_uri='file:///synthetic/other.wav')
                    elif change=='source':host.lib.source='/synthetic/other.db'
                    elif change=='library':host.lib.source_library_id='other'
                    elif change=='kind':host.lib.source_kind='rekordbox-xml'
                    elif change=='maintenance':host._state_maintenance=True
                    else:host._shutting_down=True
                    return QMessageBox.Yes
                with mock.patch.object(QMessageBox,'question',side_effect=confirm):
                    self.assertFalse(MainWindow._delete_tracks(host,['one']))
                self.assertEqual(host.hidden,{'older'})
                host._save_state.assert_not_called();host._after_track_edit.assert_not_called()
                host._discard_graveyard_analysis.assert_not_called()

    def test_decline_or_failed_storage_retains_original_owners_and_analysis(self):
        from desktop.app import QMessageBox
        for answer in (QMessageBox.No,QMessageBox.Yes):
            with self.subTest(answer=answer):
                host=self.host();hidden=host.hidden;dates=host.hidden_dates
                def save():
                    self.assertEqual(host.hidden,{'older','one'})
                    self.assertEqual(hidden,{'older'});self.assertEqual(dates,{'older':'existing date'})
                    return False
                host._save_state.side_effect=save
                with mock.patch.object(QMessageBox,'question',return_value=answer):
                    self.assertFalse(MainWindow._delete_tracks(host,['one']))
                self.assertIs(host.hidden,hidden);self.assertIs(host.hidden_dates,dates)
                host._after_track_edit.assert_not_called();host._discard_graveyard_analysis.assert_not_called()

    def test_success_saves_once_before_view_or_analysis_changes_and_keeps_newer_unrelated_hide(self):
        from desktop.app import QMessageBox
        host=self.host();order=[]
        def confirm(*_args):
            host.hidden.add('newer');host.hidden_dates['newer']='newer date'
            return QMessageBox.Yes
        host._save_state.side_effect=lambda:order.append('durable') or True
        host._after_track_edit.side_effect=lambda *_args,**_kwargs:order.append('view')
        host._discard_graveyard_analysis.side_effect=lambda *_args:order.append('analysis')
        with mock.patch.object(QMessageBox,'question',side_effect=confirm):
            self.assertTrue(MainWindow._delete_tracks(host,['one','one']))
        self.assertEqual(order,['durable','view','analysis'])
        self.assertEqual(host.hidden,{'older','newer','one'})
        self.assertEqual(host.hidden_dates['newer'],'newer date')
        host._save_state.assert_called_once()
        host._after_track_edit.assert_called_once_with('Removed 1 from Collection ✓',persist=False)
        host._discard_graveyard_analysis.assert_called_once_with(['one'])

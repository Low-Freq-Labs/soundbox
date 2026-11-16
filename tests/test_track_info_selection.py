"""Info-button targeting with distinct browser, deck and Graveyard selections."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication,QFrame,QPushButton,QStackedWidget,
                              QTabWidget,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget)

from desktop.track_info import TrackInfoMixin
from desktop.settings_page import _GRAVE_ROLE
from soundbox.library import Library,Track


class Host(TrackInfoMixin,QFrame):
    def __init__(self):
        super().__init__();layout=QVBoxLayout(self)
        self.info=QPushButton('i');layout.addWidget(self.info);self.info.clicked.connect(self._open_track_info)
        self.body=QStackedWidget();layout.addWidget(self.body)
        browser=QWidget();self.body.addWidget(browser);settings=QWidget();self.body.addWidget(settings)
        self.stack=QStackedWidget(browser);self.stack.addWidget(QWidget());self.stack.addWidget(QWidget())
        self.device_content_stack=SimpleNamespace(currentIndex=lambda:0)
        self.tabs=QTabWidget(settings);sl=QVBoxLayout(settings);sl.addWidget(self.tabs)
        self.tabs.addTab(QWidget(),'General');self._grave=QTreeWidget();self.tabs.addTab(self._grave,'Graveyard')
        self._grave.setSelectionMode(QTreeWidget.ExtendedSelection)
        tracks={tid:Track(tid,title,'',Path('/synthetic/'+tid+'.wav').as_uri()) for tid,title in (
            ('browser','Browser song'),('deck','Loaded song'),('grave-a','Hidden song'),('grave-b','Removed from playlist'))}
        self.lib=Library(tracks,[],Path('/synthetic/library.xml'))
        self._selected_track=mock.Mock(return_value=tracks['browser'])
        self.player_bar=SimpleNamespace(current_track=lambda:'deck')
        self._row_for=lambda track:dict(id=track.track_id,title=track.name,location=str(track.path))
        self.art_cache=SimpleNamespace(image_path=mock.Mock(return_value=None))
        self._art_pick=mock.Mock();self._save_track_info=mock.Mock();self._flash_status=mock.Mock()
        for tid,kind in (('grave-a','del'),('grave-b','rem')):
            item=QTreeWidgetItem([tracks[tid].name]);item.setData(0,_GRAVE_ROLE,(kind,tid,None if kind=='del' else 'playlist'))
            self._grave.addTopLevelItem(item)
        self.body.setCurrentIndex(1);self.tabs.setCurrentIndex(1)


class TrackInfoSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.host=Host();self.host.show();self.app.processEvents()
        self.dialog=mock.patch('desktop.track_info.TrackInfoDialog').start();self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.host.close)

    def click_info(self):QTest.mouseClick(self.host.info,Qt.LeftButton);self.app.processEvents()

    def test_button_uses_highlighted_graveyard_song_after_focus_moves_off_the_list(self):
        h=self.host
        for index,tid in ((0,'grave-a'),(1,'grave-b')):
            h._grave.setCurrentItem(h._grave.topLevelItem(index));h._grave.setFocus();self.app.processEvents()
            self.click_info();self.assertEqual(self.dialog.call_args.args[0]['id'],tid)
            self.dialog.call_args.kwargs['save']({'album':'Edited'})
            h._save_track_info.assert_called_with(tid,{'album':'Edited'})
        h._selected_track.assert_not_called();h.art_cache.image_path.assert_not_called()

    def test_multiple_highlights_follow_current_selected_graveyard_row(self):
        h=self.host;first=h._grave.topLevelItem(0);second=h._grave.topLevelItem(1)
        h._grave.setCurrentItem(second);first.setSelected(True);second.setSelected(True)
        self.click_info();self.assertEqual(self.dialog.call_args.args[0]['id'],'grave-b')
        second.setSelected(False);self.click_info();self.assertEqual(self.dialog.call_args.args[0]['id'],'grave-a')

    def test_no_highlight_or_missing_track_never_shows_stale_browser_or_deck_info(self):
        h=self.host;h._grave.clearSelection();self.click_info()
        self.dialog.assert_not_called();h._selected_track.assert_not_called()
        missing=QTreeWidgetItem(['Missing']);missing.setData(0,_GRAVE_ROLE,('del','missing',None))
        h._grave.addTopLevelItem(missing);h._grave.setCurrentItem(missing);self.click_info()
        self.dialog.assert_not_called();h._flash_status.assert_called_with('Track is no longer in the library')

    def test_inactive_graveyard_selection_does_not_override_normal_browser_or_loaded_fallback(self):
        h=self.host;h._grave.setCurrentItem(h._grave.topLevelItem(0))
        for settings_visible in (True,False):
            h.tabs.setCurrentIndex(0);h.body.setCurrentIndex(1 if settings_visible else 0)
            self.click_info();self.assertEqual(self.dialog.call_args.args[0]['id'],'browser')
        h._selected_track.return_value=None;self.click_info()
        self.assertEqual(self.dialog.call_args.args[0]['id'],'deck')

    def test_native_deleted_historical_row_uses_recovered_info_without_writable_live_controls(self):
        h=self.host;h.lib.tracks_by_id.pop('grave-a')
        h.graveyard_controller=SimpleNamespace(info=mock.Mock(return_value=dict(id='grave-a',title='Recovered',location='/exact.wav')))
        h._grave.setCurrentItem(h._grave.topLevelItem(0));self.click_info()
        self.assertEqual(self.dialog.call_args.args[0]['title'],'Recovered')
        self.assertIsNone(self.dialog.call_args.kwargs.get('save'));self.assertIsNone(self.dialog.call_args.kwargs.get('pick_art'))

    def test_open_dialog_rejects_source_or_song_replacement_before_saving(self):
        h=self.host;h.body.setCurrentIndex(0)
        original=h.lib;track=original.tracks_by_id['browser']
        changes={'title':'Intended title'}
        for changed in (replace(original,source=Path('/different/library.xml')),
                        replace(original,source_library_id='different'),
                        replace(original,source_kind='rekordbox-db'),
                        replace(original,tracks_by_id={**original.tracks_by_id,
                            'browser':replace(track,source_uuid='replacement')}),
                        replace(original,tracks_by_id={**original.tracks_by_id,
                            'browser':replace(track,source_native_id='replacement')}),
                        replace(original,tracks_by_id={**original.tracks_by_id,
                            'browser':replace(track,location_uri='file:///replacement.wav')}),
                        replace(original,tracks_by_id={**original.tracks_by_id,
                            'browser':replace(track,source_native_path='/native/replacement.wav')})):
            with self.subTest(source=changed.source,track=changed.tracks_by_id['browser']):
                h.lib=original;self.click_info()
                save=self.dialog.call_args.kwargs['save'];h.lib=changed
                self.assertFalse(save(changes));h._save_track_info.assert_not_called()
        h.lib=original

    def test_open_dialog_accepts_fresh_library_with_exact_same_song_identity(self):
        h=self.host;h.body.setCurrentIndex(0);self.click_info()
        save=self.dialog.call_args.kwargs['save']
        h.lib=replace(h.lib,tracks_by_id={tid:replace(track) for tid,track in h.lib.tracks_by_id.items()})
        save({'title':'Intended title'})
        h._save_track_info.assert_called_once_with('browser',{'title':'Intended title'})

"""Startup and passive UI work never construct disconnected device surfaces."""
from pathlib import Path
from dataclasses import replace
import sys
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from PySide6.QtCore import QEvent, QPointF, QSize, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtTest import QTest

from desktop.app import MainWindow
from desktop.export_dialog import ExportSelectionMixin
from desktop.trees import DEVPATH_ROLE


class UsbPreferenceDemand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.usb_device_view import UsbDevicePages,UsbSnapshot
        from desktop.devices import DeviceFacts
        from soundbox.cdj_usb import CdjLibrary
        from soundbox import my_settings
        self.pages=UsbDevicePages();self.addCleanup(self.dispose)
        files=my_settings.new_files({})
        self.profile=my_settings.values(files.items())
        self.native=CdjLibrary((),'Generated',0,0,(),0,tuple(files.items()),preferences={
            'categories':[(4,4,'TRACK',1,True),(1,1,'GENRE',0,False)],
            'sorts':[(25,25,'TRACK',1,True),(26,26,'DEFAULT',2,True),(1,1,'GENRE',0,False)],
            'sub_column':1,'colors':[(i,str(i)) for i in range(1,9)]})
        self.snapshot=UsbSnapshot(Path('/generated/no-device-io'),7,
            DeviceFacts(volume_uuid='generated'),native=self.native)
        self.device=SimpleNamespace(kind='generic_usb',label='USB',cdj_export=True)

    def dispose(self):
        self.pages.close();self.pages.deleteLater();self.app.processEvents()

    def publish(self,snapshot):
        self.pages.set_snapshot(snapshot,name='Generated',profile=self.device,free=400,drift=None)

    def test_each_preference_tab_is_complete_before_its_first_visible_frame(self):
        self.publish(self.snapshot)
        ready=[]
        def revealed(index):
            widget=self.pages.stack.currentWidget()
            if index in (2,3):
                ready.append((index,widget.active.count(),widget.inactive.count()))
            elif index==4:ready.append((index,widget.column.currentData()))
            elif index==5:ready.append((index,[edit.text() for edit in widget.controls.values()]))
            elif index==6:ready.append((index,len(widget.controls),widget.profile))
        self.pages.stack.currentChanged.connect(revealed)
        self.pages.resize(900,600);self.pages.show();self.app.processEvents()
        for index in range(2,7):self.pages.set_page(index)
        self.assertEqual(ready,[(2,1,1),(3,2,1),(4,1),(5,[str(i) for i in range(1,9)]),
                                (6,44,self.profile)])
        self.assertEqual(self.pages.stack.count(),7)
        saves=[];self.pages.auto_settings_requested.connect(lambda binding,changes:saves.append((binding,changes)))
        self.pages.set_page(5)
        edit=self.pages.colors.controls['8'];edit.setFocus();edit.selectAll()
        QTest.keyClicks(edit,'Energy')
        QTest.keyClick(edit,Qt.Key_Tab)
        self.assertEqual(saves,[((self.snapshot,'generic_usb'),{'colors':{'8':'Energy'}})])

    def test_saved_settings_tab_is_hydrated_before_snapshot_reveal(self):
        self.pages._states={'generated':{'page':6}}
        self.pages.set_global_profile(self.profile)
        self.publish(self.snapshot)
        self.assertEqual(self.pages.stack.currentIndex(),6)
        self.assertIs(self.pages.stack.currentWidget(),self.pages.my_settings)
        self.assertEqual(len(self.pages.my_settings.controls),44)
        self.assertEqual(self.pages.my_settings.profile,self.profile)
        self.assertFalse(self.pages.my_settings.save.isEnabled())
        for name in ('categories','sorts','column','colors'):
            self.assertNotIn(name,vars(self.pages))

    def test_unopened_tabs_keep_latest_valid_data_through_notice_and_global_update(self):
        from soundbox import my_settings
        self.publish(self.snapshot)
        files=my_settings.new_files({'MYSETTING.DAT':{'tempo_range':'wide'}})
        updated=replace(self.snapshot,native=replace(self.native,files=tuple(files.items())))
        self.publish(updated)
        self.publish(replace(updated,native=None,notice='Select a tab to read the USB'))
        self.pages.set_global_profile(self.profile)
        for name in ('categories','sorts','column','colors','my_settings'):
            self.assertNotIn(name,vars(self.pages))
        self.pages.set_page(6)
        self.assertEqual(self.pages.my_settings.profile,my_settings.values(files.items()))
        self.assertEqual(self.pages.my_settings.global_profile,self.profile)
        self.pages.set_page(2)
        self.assertEqual(self.pages.categories.order(),[4])

    def test_settings_draft_survives_same_device_refresh_but_never_another_device(self):
        self.publish(self.snapshot);self.pages.set_page(6)
        editor=self.pages.my_settings
        control=editor.controls['MYSETTING.DAT','tempo_range']
        control.setCurrentIndex(control.findData('wide'))
        self.assertTrue(editor.save.isEnabled())
        self.publish(replace(self.snapshot))
        self.assertEqual(editor.current()['MYSETTING.DAT']['tempo_range'],'wide')
        other=replace(self.snapshot,target=Path('/generated/other-device'),
                      facts=replace(self.snapshot.facts,volume_uuid='other'))
        self.publish(other)
        self.assertEqual(editor.current(),self.profile)
        self.assertFalse(editor.save.isEnabled())

    def test_equal_device_preferences_keep_own_drafts_and_reset_on_device_switch(self):
        self.publish(self.snapshot)
        self.pages.controls['device_name'].setText('Own unsaved name')
        categories=self.pages.categories
        categories.inactive.setCurrentRow(0);categories._move(categories.inactive,categories.active)
        self.pages.colors.controls['8'].setText('Own color')
        self.publish(replace(self.snapshot))
        self.assertEqual(self.pages.controls['device_name'].text(),'Own unsaved name')
        self.assertEqual(categories.order(),[4,1])
        self.assertEqual(self.pages.colors.controls['8'].text(),'Own color')
        self.publish(replace(self.snapshot,target=Path('/generated/other-device'),
                             facts=replace(self.snapshot.facts,volume_uuid='other')))
        self.assertEqual(self.pages.controls['device_name'].text(),'Generated')
        self.assertEqual(categories.order(),[4])
        self.assertEqual(self.pages.colors.controls['8'].text(),'8')

    def test_text_auto_apply_keeps_original_attachment_and_latest_name_through_older_ack(self):
        self.publish(self.snapshot);self.pages.show();self.app.processEvents()
        edits=[]
        self.pages.auto_settings_requested.connect(lambda binding,changes:edits.append((binding,changes)))
        name=self.pages.controls['device_name'];name.setFocus();name.selectAll();QTest.keyClicks(name,'First')
        self.pages.flush_auto_settings()
        QTest.keyClick(name,Qt.Key_A,Qt.ControlModifier);name.selectAll();QTest.keyClicks(name,'Latest')
        acknowledged=replace(self.snapshot,native=replace(self.native,name='First'))
        self.publish(acknowledged)
        self.assertEqual(self.pages.controls['device_name'].text(),'Latest')
        self.assertEqual(edits[0],((self.snapshot,'generic_usb'),{'device_name':'First'}))
        self.assertEqual(edits[1],((self.snapshot,'generic_usb'),{'device_name':'Latest'}))
        other=replace(self.snapshot,target=Path('/generated/another'),
                      facts=replace(self.snapshot.facts,volume_uuid='another'))
        self.publish(other)
        self.assertEqual(self.pages.controls['device_name'].text(),'Generated')
        self.assertEqual(len(edits),2)

    def test_auto_choice_can_undo_a_queued_choice_without_hydration_emitting_edits(self):
        self.publish(self.snapshot);self.pages.set_page(6)
        edits=[];self.pages.auto_settings_requested.connect(lambda binding,changes:edits.append(changes))
        control=self.pages.my_settings.controls['MYSETTING.DAT','tempo_range']
        original=control.currentData()
        control.setCurrentIndex(control.findData('wide'));control.activated.emit(control.currentIndex())
        control.setCurrentIndex(control.findData(original));control.activated.emit(control.currentIndex())
        self.publish(replace(self.snapshot))
        self.assertEqual(edits,[{'my_settings':{'MYSETTING.DAT':{'tempo_range':'wide'}}},
                                {'my_settings':{'MYSETTING.DAT':{'tempo_range':original}}}])


class DeviceDemand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, *, providers=None, initial_state=None):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        # Suppress even the constructor's discovery request; every target below
        # is a synthetic profile and no native source/provider is admitted.
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        win = MainWindow(providers=providers, isolated_dir=Path(area.name),initial_state=initial_state)
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.shutdown)
        return win

    def test_passive_startup_resize_theme_and_shutdown_keep_device_engines_absent(self):
        with (
            mock.patch('desktop.app.UsbDevicePages', side_effect=AssertionError('USB surface before demand')),
            mock.patch('desktop.app.StockIpodPages', side_effect=AssertionError('iPod surface before demand')),
        ):
            win = self.window()
            win.show(); self.app.processEvents()
            win.resize(1300, 820); self.app.processEvents()
            win._sync_artist_columns(); win._fit_browser_columns(fill=True)
            win._apply_theme_color('#123456', save=False)
            win._update_device_action_state()
            self.assertFalse(win.device_sync_btn.isEnabled())
            self.assertFalse(win.eject_btn.isEnabled())
            for name in ('usb_device_pages', 'stock_ipod_pages', 'stock_art_cache',
                         'rockbox_media_cache', 'rockbox_art_cache', 'stock_ipod_shadow_store'):
                self.assertNotIn(name, vars(win))
            self.assertTrue(hasattr(win, '_settings_tabs'))
            self.assertEqual(win._settings_tabs.count(), 6)
            win.shutdown()
            self.assertNotIn('usb_device_pages', vars(win))

    def select(self, win, profile):
        path = '/synthetic/device'
        win._device_profiles[path] = profile
        item = QTreeWidgetItem(['Synthetic']); item.setData(0, DEVPATH_ROLE, path)
        win.device_tree.addTopLevelItem(item)
        return item

    def test_usb_view_constructs_only_usb_with_independent_playlist_geometry(self):
        win = self.window()
        item = self.select(win, SimpleNamespace(kind='generic_usb', has_stock_ipod=False))
        win.table.setColumnWidth(0, 417)
        win.table.setColumnWidth(1, 137)
        win.table.setColumnHidden(2, True)
        win._on_device_click(item, 0)
        pages = win.usb_device_pages
        self.assertIs(win.device_content_stack.widget(2), pages)
        self.assertEqual(win.device_content_stack.currentIndex(), 2)
        tree = pages.playlist_tree
        self.assertEqual(tree.columnCount(), 3)
        self.assertEqual([tree.headerItem().text(i) for i in range(3)],
                         ['Playlist / Track', 'Tracks', 'Type'])
        self.assertEqual((tree.columnWidth(1), tree.columnWidth(2)), (70, 150))
        self.assertFalse(tree.isColumnHidden(2))
        self.assertNotIn('stock_ipod_pages', vars(win))
        self.assertIs(win.usb_device_pages, pages)
        self.assertEqual(win.device_content_stack.count(), 3)
        for name in ('categories','sorts','column','colors','my_settings'):
            self.assertNotIn(name,vars(pages))

    def test_ipod_view_constructs_only_ipod_before_queued_read(self):
        win = self.window()
        item = self.select(win, SimpleNamespace(kind='stock_ipod', has_stock_ipod=True))
        with mock.patch.object(win, '_read_stock_device_view') as reader:
            win._on_device_click(item, 0)
            reader.assert_called_once()
        self.assertIs(win.device_content_stack.widget(1), win.stock_ipod_pages)
        self.assertEqual(win.device_content_stack.currentIndex(), 1)
        self.assertNotIn('usb_device_pages', vars(win))

    def test_first_device_visit_ignores_last_launch_tab_but_later_visits_keep_current_tab(self):
        from desktop.usb_device_view import UsbSnapshot
        from desktop.devices import DeviceFacts
        from soundbox.stock_ipod_info import StockIpodSnapshot
        for stock in (False,True):
            with self.subTest(stock=stock):
                remembered={'page':2 if stock else 6,'views':{},'expanded':[],'checked':[],
                            'selected':'','scroll':0,'overview_scroll':0}
                saved={'device_views':{'stock' if stock else 'usb':{'generated':remembered}}}
                win=self.window(initial_state={'settings':saved})
                item=self.select(win,SimpleNamespace(kind='stock_ipod' if stock else 'generic_usb',
                                                     has_stock_ipod=stock))
                path=item.data(0,DEVPATH_ROLE)
                if stock:
                    snapshot=StockIpodSnapshot(Path(path),'Generated','generated','FAT32','USB','fixture',
                        True,1000,500,'','','iPod','Unknown','generated',1,2,0x19,0,'hash',0,0,'',0,(),())
                    win._stock_view_cache[path]=(snapshot,None)
                else:
                    win._usb_view_cache[path]=UsbSnapshot(Path(path),7,DeviceFacts(volume_uuid='generated'))
                with mock.patch.object(win,'_schedule_stock_artwork'):
                    win._on_device_click(item,0)
                    pages=win.stock_ipod_pages if stock else win.usb_device_pages
                    self.assertEqual(pages.stack.currentIndex(),0)
                    win._set_device_tab(2 if stock else 1)
                    win.stack.setCurrentIndex(0)
                    win._on_device_click(item,0)
                    self.assertEqual(pages.stack.currentIndex(),2 if stock else 1)

    def test_explicit_device_open_prepares_settings_without_revealing_it_or_building_stock(self):
        from desktop.usb_device_view import UsbSnapshot
        from desktop.devices import DeviceFacts
        from soundbox.cdj_usb import CdjLibrary
        from soundbox import my_settings
        win=self.window();item=self.select(win,SimpleNamespace(kind='generic_usb',has_stock_ipod=False))
        path=item.data(0,DEVPATH_ROLE);files=my_settings.new_files({})
        native=CdjLibrary((),'Generated',0,0,(),0,tuple(files.items()))
        win._usb_view_cache[path]=UsbSnapshot(Path(path),7,DeviceFacts(volume_uuid='generated'),native=native)
        win._on_device_click(item,0)
        pages=win.usb_device_pages
        self.assertEqual(pages.stack.currentIndex(),0)
        self.wait_for(lambda:bool(getattr(vars(pages).get('_preparing_my_settings'),'prepared',False)))
        self.assertEqual(pages.stack.currentIndex(),0)
        self.assertEqual(pages.my_settings.profile,my_settings.values(files.items()))
        self.assertEqual(len(pages.my_settings.controls),44)
        self.assertNotIn('stock_ipod_pages',vars(win))

    def test_settings_ready_before_show_first_open_uses_current_preferences_and_preserves_view(self):
        win = self.window()
        self.assertFalse(win.isVisible())
        self.assertEqual(win._desktop_runtime_errors, ())
        from soundbox.desktop_runtime import DESKTOP_MODULES
        self.assertTrue(all(name in sys.modules for name in DESKTOP_MODULES))
        self.assertEqual([win._settings_tabs.tabText(i) for i in range(win._settings_tabs.count())],
                         ['General', 'Analysis', 'My Settings', 'Keyboard', 'Graveyard', 'Connections'])
        self.assertEqual(len(win._global_my_settings.controls), 44)
        self.assertIsNotNone(win._keyboard_page)
        self.assertIsNotNone(win._analysis_controls)
        win._apply_theme_text('custom', '#112233', save=False)
        win.settings['hide_untracked'] = True
        self.assertTrue(hasattr(win, '_settings_tabs'))
        with mock.patch.object(win, '_settings_page', side_effect=AssertionError('First click built Settings')):
            win._open_settings()
        page = win.body_stack.widget(1)
        self.assertEqual(win.body_stack.currentIndex(), 1)
        self.assertEqual(win._theme_text_mode.currentData(), 'custom')
        self.assertEqual(win._theme_text_color.text(), '#112233')
        self.assertTrue(win._chk_hide.isChecked())
        win._close_settings(); win._open_settings()
        self.assertIs(win.body_stack.widget(1), page)
        self.assertEqual(win.body_stack.count(), 2)
        self.assertNotIn('usb_device_pages', vars(win))
        self.assertNotIn('stock_ipod_pages', vars(win))

    def test_export_without_capable_targets_never_builds_playlist_inventory_or_dialog(self):
        host = SimpleNamespace(lib=SimpleNamespace(source='fixture', source_library_id='fixture'),
                               _device_profiles={}, _device_operation_owned=lambda _: False,
                               _flash_status=mock.Mock(),
                               _export_playlists=mock.Mock(side_effect=AssertionError('undemanded playlist inventory')))
        with mock.patch('desktop.export_dialog.ExportDialog', side_effect=AssertionError('disconnected Export dialog')):
            ExportSelectionMixin._show_export_dialog(host)
        host._export_playlists.assert_not_called()
        host._flash_status.assert_called_once()

    def test_unfocused_shortcuts_do_not_treat_absent_graveyard_as_selection(self):
        win = self.window()
        with mock.patch.object(QApplication, 'focusWidget', return_value=None):
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier)
            win.eventFilter(win.table, event)
            win._dispatch_keyboard_action('browser.load')
        self.assertTrue(hasattr(win, '_grave'))
        self.assertTrue(hasattr(win, '_settings_tabs'))

    def test_construction_events_skip_input_work_and_read_event_type_once(self):
        win = self.window()
        class CountedEvent(QEvent):
            calls = 0
            def type(self):
                self.calls += 1
                return super().type()
        event = CountedEvent(QEvent.StyleChange)
        with mock.patch.object(win, '_defer_library_prep') as defer, \
                mock.patch.object(win._keyboard_router, 'handle') as keyboard:
            self.assertFalse(win.eventFilter(win.table, event))
        self.assertEqual(event.calls, 1)
        defer.assert_not_called(); keyboard.assert_not_called()
        self.assertTrue(hasattr(win, '_settings_tabs'))
        self.assertNotIn('usb_device_pages', vars(win))

    def test_geometry_and_now_playing_mouse_routes_survive_event_filter_fast_path(self):
        win = self.window(); win._browser_columns_shown = True
        viewport = win.table.viewport()
        with mock.patch.object(win, '_fit_browser_columns') as fit, \
                mock.patch.object(win, '_defer_library_prep') as defer:
            win.eventFilter(viewport, QEvent(QEvent.Show))
            fit.assert_called_once_with(fill=True, available=viewport.width())
            # A hidden viewport receives its geometry on Show; Resize still
            # defers background preparation regardless of its visibility.
            win.eventFilter(viewport, QResizeEvent(QSize(701, 300), QSize(700, 300)))
            defer.assert_called_once_with(0.75)
        release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2),
                              Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        with mock.patch.object(win, '_focus_now_playing') as focus:
            self.assertTrue(win.eventFilter(win.now_playing, release))
        focus.assert_called_once()

    def test_key_release_reaches_router_with_ready_settings(self):
        win = self.window()
        event = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
        with mock.patch.object(QApplication, 'focusWidget', return_value=win.table), \
                mock.patch.object(win, '_space_should_play', return_value=True), \
                mock.patch.object(win._keyboard_router, 'handle', return_value=True) as keyboard:
            self.assertTrue(win.eventFilter(win.table, event))
        keyboard.assert_called_once_with(event, True)
        self.assertTrue(hasattr(win, '_settings_tabs'))

    def test_saved_global_usb_profile_does_not_construct_device_surface(self):
        from soundbox import my_settings
        win = self.window()
        win._open_settings()
        profile = my_settings.values(my_settings.new_files({}).items())
        win._save_global_my_settings(profile)
        self.assertEqual(win.settings['cdj_my_settings'], profile)
        self.assertEqual(win._global_my_settings.profile, profile)
        self.assertNotIn('usb_device_pages', vars(win))

    def test_global_usb_profile_updates_do_not_build_hidden_device_editor(self):
        from desktop.usb_device_view import UsbSnapshot
        from desktop.devices import DeviceFacts
        from soundbox.cdj_usb import CdjLibrary
        from soundbox import my_settings
        win=self.window()
        item=self.select(win,SimpleNamespace(kind='generic_usb',has_stock_ipod=False))
        path=item.data(0,DEVPATH_ROLE)
        files=my_settings.new_files({})
        profile=my_settings.values(files.items())
        native=CdjLibrary((),'Generated',0,0,(),0,tuple(files.items()))
        snapshot=UsbSnapshot(Path(path),7,DeviceFacts(volume_uuid='generated'),native=native)
        win.settings['cdj_my_settings']=profile
        win._usb_view_cache[path]=snapshot
        win._on_device_click(item,0)
        pages=win.usb_device_pages
        self.assertNotIn('my_settings',vars(pages))
        fresh={name:dict(values) for name,values in profile.items()}
        fresh['MYSETTING.DAT']['tempo_range']='wide'
        win._save_global_my_settings(fresh)
        self.assertNotIn('my_settings',vars(pages))
        failed={name:dict(values) for name,values in fresh.items()}
        failed['MYSETTING.DAT']['tempo_range']='six'
        with mock.patch.object(win,'_save_state',return_value=False):
            win._save_global_my_settings(failed)
        self.assertEqual(win.settings['cdj_my_settings'],fresh)
        self.assertEqual(pages.global_profile,fresh)
        self.assertNotIn('my_settings',vars(pages))
        observed=[]
        pages.stack.currentChanged.connect(lambda index:observed.append(
            (index,len(pages.my_settings.controls),pages.my_settings.global_profile)) if index==6 else None)
        win._set_device_tab(6)
        self.assertEqual(observed,[(6,44,fresh)])
        self.assertEqual(pages.my_settings.profile,profile)
        self.assertIs(pages.stack.currentWidget(),pages.my_settings)

    def start_connection(self, win, service):
        factory = win.providers.connections
        self.assertIs(factory.create.return_value, service)
        # Only admit this controlled provider operation. The window remains an
        # isolated fixture for every other source/device/background path.
        with mock.patch.object(win, '_isolated', False), \
                mock.patch('desktop.app.QStandardPaths.isTestModeEnabled', return_value=False):
            win._prepare_connections()
            win._prepare_connections()
        return factory

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(2)
        self.assertTrue(predicate())

    def test_connection_refresh_without_settings_stays_owned_and_settings_can_cancel(self):
        from soundbox.providers import Providers
        entered = Event()

        def refresh(stop, progress):
            entered.set()
            stop.wait(3)

        service = SimpleNamespace(refresh_initial=mock.Mock(side_effect=refresh),
                                  status=lambda: dict(connected=True, count=3))
        win = self.window(providers=Providers('personal', actions=('connections.soundcloud',),
            connections=SimpleNamespace(create=mock.Mock(return_value=service))))
        factory = self.start_connection(win, service)
        self.assertTrue(entered.wait(3))
        self.assertTrue(hasattr(win, '_settings_tabs'))
        worker = win._connection_initial_worker
        self.assertEqual(win._connection_tasks.pending, 1)
        win._open_settings()
        section = win._soundcloud_section
        self.assertIs(section._startup_worker, worker)
        self.assertFalse(section.refresh_button.isEnabled())
        section._cancel()
        self.wait_for(lambda: win._connection_initial_worker is None)
        self.assertTrue(worker.cancellation.is_set())
        self.assertTrue(win._connection_initial_complete)
        self.assertIsNone(win._connection_initial_result)
        self.assertTrue(section.refresh_button.isEnabled())
        self.assertEqual(win._connection_tasks.pending, 0)
        factory.create.assert_called_with(win._data_file.parent)
        service.refresh_initial.assert_called_once()
        with mock.patch.object(win, '_isolated', False), \
                mock.patch('desktop.app.QStandardPaths.isTestModeEnabled', return_value=False):
            win._prepare_connections()
        service.refresh_initial.assert_called_once()

    def test_connection_failure_before_settings_open_is_retained_without_retry(self):
        from soundbox.providers import ConnectionFailure, Providers
        service = SimpleNamespace(refresh_initial=mock.Mock(side_effect=ConnectionFailure('Offline fixture')),
                                  status=lambda: dict(connected=True, count=3))
        win = self.window(providers=Providers('personal', actions=('connections.soundcloud',),
            connections=SimpleNamespace(create=mock.Mock(return_value=service))))
        factory = self.start_connection(win, service)
        self.wait_for(lambda: getattr(win, '_connection_initial_complete', False))
        self.assertTrue(hasattr(win, '_settings_tabs'))
        win._open_settings()
        self.assertEqual(win._soundcloud_section.status.text(), 'Offline fixture')
        self.assertTrue(win._soundcloud_section.refresh_button.isEnabled())
        service.refresh_initial.assert_called_once()
        self.assertGreaterEqual(factory.create.call_count, 1)

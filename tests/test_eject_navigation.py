"""Successful device retirement selects the closest surviving logical entry."""
from pathlib import Path
from unittest import mock
import unittest

from desktop.device_browser import DeviceBrowserMixin
from desktop.devices import DeviceFacts
from desktop.trees import COLLECTION_ROLE,DEVPATH_ROLE,DEVROLE_ROLE
from soundbox.device_profile import DeviceProfile
from tests import test_device_navigation_publication as navigation


class EjectNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        navigation.DeviceNavigationPublication.setUpClass()
        cls.app=navigation.DeviceNavigationPublication.app

    def setUp(self):
        case=navigation.DeviceNavigationPublication();case.setUp();self.addCleanup(case.doCleanups)
        self.w=case.window;self.root=self.w._data_file.parent
        self.enterContext(mock.patch('desktop.app.device_fs',return_value='msdos'))
        self.enterContext(mock.patch('desktop.app.device_facts',return_value=DeviceFacts(volume_uuid='generated',writable=True)))
        self.enterContext(mock.patch('desktop.devices.forget_device_mount'))
        self.enterContext(mock.patch.object(self.w,'_read_stock_device_view'))
        self.generic=DeviceProfile('generic_usb','USB','msdos',False,False,False,False,False)
        self.stock=DeviceProfile('stock_ipod','iTunes','msdos',False,True,False,True,True)
        self.paths={name:str(self.root/name) for name in ('FIRST','SONNY','LAST')}
        for path in self.paths.values():Path(path).mkdir()
        self.scan()

    def scan(self):
        self.w._on_devices_scanned([(name,path,50000,0,self.stock if name=='SONNY' else self.generic)
            for name,path in self.paths.items()],mount_tokens={path:Path(path).stat().st_dev for path in self.paths.values()})

    def row(self,name,role=''):
        return next(self.w.device_tree.topLevelItem(i) for i in range(self.w.device_tree.topLevelItemCount())
                    if self.w.device_tree.topLevelItem(i).data(0,DEVPATH_ROLE)==self.paths[name]
                    and (self.w.device_tree.topLevelItem(i).data(0,DEVROLE_ROLE) or '')==role)

    def select(self,name,role=''):
        item=self.row(name,role);self.w.device_tree.setCurrentItem(item);self.w._on_device_click(item,0)

    def test_ejecting_either_ipod_role_selects_next_usb_and_removes_both_rows(self):
        for role in ('','ipod_usb'):
            with self.subTest(role=role):
                self.scan();self.select('SONNY',role)
                DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['SONNY'])
                self.assertEqual(self.w._current_device_path,self.paths['LAST'])
                self.assertEqual(self.w._current_device_role,'')
                self.assertEqual(self.w.stack.currentIndex(),1)
                self.assertIs(self.w.device_tree.currentItem(),self.row('LAST'))
                self.assertEqual(self.w.device_content_stack.currentIndex(),2)
                self.assertEqual(self.w.device_sync_btn.text(),'Export')
                self.assertEqual(self.w.device_tree.topLevelItemCount(),2)

    def test_last_usb_uses_nearest_previous_ipod_usb_role(self):
        self.select('LAST');DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['LAST'])
        self.assertEqual((self.w._current_device_path,self.w._current_device_role),(self.paths['SONNY'],'ipod_usb'))
        self.assertIs(self.w.device_tree.currentItem(),self.row('SONNY','ipod_usb'))
        self.assertEqual(self.w.device_sync_btn.text(),'Export')

    def test_first_usb_uses_next_stock_ipod_role(self):
        self.select('FIRST');DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['FIRST'])
        self.assertEqual((self.w._current_device_path,self.w._current_device_role),(self.paths['SONNY'],''))
        self.assertIs(self.w.device_tree.currentItem(),self.row('SONNY'))
        self.assertEqual(self.w.device_content_stack.currentIndex(),1)

    def test_ejecting_an_unselected_mount_keeps_current_page_and_role(self):
        self.select('SONNY','ipod_usb');self.w._set_device_tab(1)
        item=self.w.device_tree.currentItem()
        DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['FIRST'])
        self.assertEqual((self.w._current_device_path,self.w._current_device_role),(self.paths['SONNY'],'ipod_usb'))
        self.assertIs(self.w.device_tree.currentItem(),item)
        self.assertEqual(self.w.usb_device_pages.stack.currentIndex(),1)

    def test_no_devices_remaining_selects_and_presents_collection(self):
        for name in ('FIRST','LAST'):DeviceBrowserMixin._retire_ejected_device(self.w,self.paths[name])
        self.select('SONNY','ipod_usb');DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['SONNY'])
        self.assertEqual(self.w.device_tree.topLevelItemCount(),0)
        self.assertEqual(self.w.stack.currentIndex(),0)
        self.assertEqual((self.w._current_device_path,self.w._current_device_role),('',''))
        self.assertTrue(self.w.sb_tree.currentItem().data(0,COLLECTION_ROLE))
        self.assertTrue(self.w.sb_tree.currentItem().isSelected())
        self.assertEqual(self.w._view_key,'sb:Collection')

    def test_library_navigation_during_eject_is_not_stolen_by_a_remaining_device(self):
        self.select('SONNY','ipod_usb');collection=self.w.sb_tree.topLevelItem(0)
        self.w._on_custom_select(collection,None)
        self.assertEqual(self.w.stack.currentIndex(),0)
        DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['SONNY'])
        self.assertEqual(self.w.stack.currentIndex(),0)
        self.assertEqual(self.w._view_key,'sb:Collection')

    def test_artists_navigation_during_eject_preserves_its_actual_page_and_sidebar(self):
        self.select('SONNY','ipod_usb');artists=self.w.sb_tree.topLevelItem(1)
        self.w.sb_tree.setCurrentItem(artists);self.w._on_custom_select(artists,None)
        before=(self.w.stack.currentIndex(),self.w.body_stack.currentIndex(),self.w._view_key,
                self.w.sb_tree.currentItem(),tuple(self.w.sb_tree.selectedItems()))
        self.assertEqual(before[0],2)
        DeviceBrowserMixin._retire_ejected_device(self.w,self.paths['SONNY'])
        self.assertEqual((self.w.stack.currentIndex(),self.w.body_stack.currentIndex(),self.w._view_key,
                          self.w.sb_tree.currentItem(),tuple(self.w.sb_tree.selectedItems())),before)

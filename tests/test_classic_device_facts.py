"""Generated mounted-Classic identity acquisition; no real macOS/device probes."""
from pathlib import Path
import plistlib
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop import devices
from soundbox.stock_ipod_identity import StockIdentityObservation


GUID = '000A270000000001'
OTHER_GUID = '000A270000000002'
SERIAL = 'ZZ1134H59ZU'
OTHER_SERIAL = 'ZZ650V0HV9M'


def usb(serial=GUID, *, bsd='disk7s1', product=0x1261, entry=101):
    whole=bsd.split('s')[0]
    return dict(IOObjectClass='IOUSBHostDevice', idVendor=1452, idProduct=product,
        IORegistryEntryID=entry, **{'USB Product Name':'iPod','USB Serial Number':serial},
        IORegistryEntryChildren=[dict(IOObjectClass='IOUSBMassStorageDriver',
            **{'Vendor Identification':'Apple','Product Identification':'iPod',
               'Product Revision Level':'1.70'},
            IORegistryEntryChildren=[dict(IOObjectClass='IOMedia',**{'BSD Name':whole},
                IORegistryEntryChildren=[dict(IOObjectClass='IOMedia',**{'BSD Name':bsd})])])])


class ClassicDeviceFacts(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory(prefix='soundbox-classic-facts-')
        self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name).resolve();self.volume=self.root/'Classic'
        self.device=self.volume/'iPod_Control/Device';self.device.mkdir(parents=True)
        self.preferences=self.root/'com.apple.iPod.plist'
        self.write_preferences({GUID:self.record()})
        self.enterContext(mock.patch.multiple(devices,_EXTERNAL_CACHE={},_FS_CACHE={},
            _PROFILE_CACHE={},_INFO_CACHE={str(self.volume):self.info()},_IPOD_FACTS_CACHE={},
            _USB_IDENTITY_CACHE={},_USB_IDENTITY_RETRY={},_IPOD_FACTS_OWNERS={}))
        self.usb_identity=self.enterContext(mock.patch.object(devices,'_usb_identity_for_volume',return_value=None))
        self.probe=self.enterContext(mock.patch.object(devices,'_ioreg_records',return_value=[usb()]))
        preferences=devices._classic_ipod_preferences
        self.finder=self.enterContext(mock.patch.object(devices,'_classic_ipod_preferences',
            side_effect=lambda serial,**options:preferences(serial,self.preferences,**options)))
        self.enterContext(mock.patch.object(devices,'_is_external',
            side_effect=AssertionError('Unexpected actual diskutil probe')))

    def record(self, serial=SERIAL, firmware='2.0.4'):
        return {'Device Class':'iPod','Serial Number':serial,'Firmware Version String':firmware}

    def info(self, *, bsd='disk7s1', uuid='generated-A', media='Generic'):
        return dict(DeviceIdentifier=bsd,VolumeUUID=uuid,MediaType=media,
                    WritableVolume=True,WritableMedia=True,BusProtocol='USB')

    def write_preferences(self, records):
        self.preferences.write_bytes(plistlib.dumps({'Devices':records}))

    def sysinfo(self, **fields):
        (self.device/'SysInfoExtended').write_bytes(plistlib.dumps(fields))

    def test_direct_access_generic_media_still_acquires_exact_classic_facts(self):
        self.sysinfo(ModelNumStr='MC297LL/A',SerialNumber=SERIAL,FirmwareVersion='2.0.4')
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.usb_family,facts.model_number),('iPod Classic','MC297'))
        self.assertEqual((facts.hardware_serial,facts.firmware_version),(SERIAL,'2.0.4'))
        self.assertEqual((facts.model_source,facts.serial_source,facts.firmware_source),
                         ('SysInfoExtended',)*3)
        self.assertEqual(facts.transport_revision,'1.70')
        self.assertEqual(facts.firewire_guid,GUID)
        self.assertFalse(facts.identity_conflict)

    def test_exact_finder_classic_record_survives_generic_media_without_guessing_model(self):
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.usb_serial,facts.hardware_serial,facts.firmware_version),
                         (GUID,SERIAL,'2.0.4'))
        self.assertEqual(facts.serial_source,'macOS iPod device record')
        self.assertEqual((facts.model_number,facts.model_source),('',''))
        self.assertNotEqual(facts.firmware_version,facts.transport_revision)

    def test_apple_ipod_descendant_without_revision_retains_scsi_transport_revision(self):
        for revision in (None,'','2.10'):
            with self.subTest(revision=revision):
                devices._IPOD_FACTS_CACHE.clear()
                tree=usb();scsi=tree['IORegistryEntryChildren'][0]
                driver=dict(IOObjectClass='com_apple_driver_iPodSBC',
                    **{'Vendor Identification':'Apple','Product Identification':'iPod'},
                    IORegistryEntryChildren=scsi['IORegistryEntryChildren'])
                if revision is not None:driver['Product Revision Level']=revision
                scsi['IORegistryEntryChildren']=[driver];self.probe.return_value=[tree]
                facts=devices.device_facts(self.volume)
                self.assertEqual(facts.transport_revision,revision or '1.70')
                self.assertEqual(facts.firmware_version,'2.0.4')
                self.assertEqual(facts.firewire_guid,GUID)

    def test_cached_stock_profile_does_not_require_media_type_or_layout_stat(self):
        devices._PROFILE_CACHE[str(self.volume)]=SimpleNamespace(has_stock_ipod=True)
        with mock.patch.object(Path,'is_dir',side_effect=AssertionError('Layout stat after profile')):
            facts=devices.device_facts(self.volume)
        self.assertEqual(facts.hardware_serial,SERIAL)

    def test_empty_first_finder_result_recovers_without_reprobing_observed_usb(self):
        self.write_preferences({})
        first=devices.device_facts(self.volume)
        self.assertEqual((first.hardware_serial,first.firmware_version),('',''))
        self.write_preferences({GUID:self.record()})
        second=devices.device_facts(self.volume)
        self.assertEqual((second.hardware_serial,second.firmware_version),(SERIAL,'2.0.4'))
        self.assertEqual(self.probe.call_count,1)
        self.assertEqual(self.finder.call_count,2)

    def test_sysinfo_created_after_first_usb_observation_is_not_permanently_missing(self):
        self.assertEqual(devices.device_facts(self.volume).model_number,'')
        self.sysinfo(ModelNumStr='MC297LL/A',SerialNumber=SERIAL,FirmwareVersion='2.0.4')
        facts=devices.device_facts(self.volume)
        self.assertEqual(facts.model_number,'MC297')
        self.assertEqual(facts.model_source,'SysInfoExtended')
        self.assertEqual(self.probe.call_count,1)

    def test_incomplete_usb_identity_is_retried_on_the_same_bsd_and_uuid(self):
        self.probe.side_effect=[[usb('')],[usb()]]
        first=devices.device_facts(self.volume)
        self.assertEqual((first.usb_serial,first.firewire_guid),('',''))
        second=devices.device_facts(self.volume)
        self.assertEqual((second.usb_serial,second.firewire_guid),(GUID,GUID))
        self.assertEqual(self.probe.call_count,2)

    def test_disk_mode_etched_serial_matches_unique_finder_record_without_minting_guid(self):
        self.probe.return_value=[usb(SERIAL)]
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.usb_serial,facts.hardware_serial,facts.firmware_version),
                         (SERIAL,SERIAL,'2.0.4'))
        self.assertEqual(facts.firewire_guid,'')
        self.assertEqual(facts.usb_family,'iPod Classic')

    def test_fresh_sysinfo_serial_can_bind_exact_finder_record_when_usb_ancestry_misses(self):
        self.probe.return_value=[]
        self.sysinfo(ModelNumStr='MC297',SerialNumber=SERIAL)
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.hardware_serial,facts.serial_source),(SERIAL,'SysInfoExtended'))
        self.assertEqual((facts.firmware_version,facts.firmware_source),
                         ('2.0.4','macOS iPod device record'))
        self.assertEqual((facts.firewire_guid,facts.usb_serial),('',''))
        cached=devices.device_facts(self.volume,cached_only=True)
        self.assertEqual((cached.model_number,cached.hardware_serial),('MC297',SERIAL))

    def test_two_ipods_with_same_label_keep_their_own_bsd_ancestry_and_finder_record(self):
        other=self.root/'Other';(other/'iPod_Control').mkdir(parents=True)
        devices._INFO_CACHE[str(other)]=self.info(bsd='disk8s1',uuid='generated-B')
        self.write_preferences({GUID:self.record(),OTHER_GUID:self.record(OTHER_SERIAL,'1.3')})
        self.probe.return_value=[usb(OTHER_GUID,bsd='disk8s1',product=0x1209,entry=202),usb()]
        classic=devices.device_facts(self.volume);video=devices.device_facts(other)
        self.assertEqual((classic.hardware_serial,classic.firmware_version,classic.firewire_guid),
                         (SERIAL,'2.0.4',GUID))
        self.assertEqual((video.hardware_serial,video.firmware_version,video.firewire_guid),
                         (OTHER_SERIAL,'1.3',OTHER_GUID))

    def test_unrelated_sole_history_and_ambiguous_serial_records_never_fill_missing_fields(self):
        self.write_preferences({OTHER_GUID:self.record(OTHER_SERIAL,'1.3')})
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.hardware_serial,facts.firmware_version),('',''))
        self.probe.return_value=[usb(SERIAL)]
        devices._IPOD_FACTS_CACHE.clear()
        self.write_preferences({GUID:self.record(),OTHER_GUID:self.record()})
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.hardware_serial,facts.firmware_version),('',''))
        self.assertEqual(facts.firewire_guid,'')

    def test_exact_usb_record_refuses_conflicting_current_sysinfo_serial(self):
        self.write_preferences({GUID:self.record(OTHER_SERIAL,'1.3')})
        self.sysinfo(ModelNumStr='MC297',SerialNumber=SERIAL)
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.hardware_serial,facts.serial_source),(SERIAL,'SysInfoExtended'))
        self.assertEqual((facts.firmware_version,facts.firmware_source),('',''))
        self.assertEqual(facts.firewire_guid,GUID)

    def test_conflict_marker_prevents_finder_refill_and_survives_cached_only_reads(self):
        (self.device/'SysInfoExtended').write_text('ModelNumStr: MC297\nModelNumStr: MA446\n')
        facts=devices.device_facts(self.volume)
        self.assertTrue(facts.identity_conflict)
        self.assertEqual((facts.hardware_serial,facts.firmware_version),('',''))
        self.assertEqual(facts.firewire_guid,GUID)
        self.finder.assert_not_called()
        self.assertTrue(devices.device_facts(self.volume,cached_only=True).identity_conflict)
        self.sysinfo(ModelNumStr='MC297')
        fresh=devices.device_facts(self.volume)
        self.assertFalse(fresh.identity_conflict)
        self.assertEqual((fresh.model_number,fresh.hardware_serial),('MC297',SERIAL))

    def test_pure_conflict_replaces_prior_model_only_cache_without_usb_ancestry(self):
        self.probe.return_value=[]
        self.sysinfo(ModelNumStr='MC297')
        before=devices.device_facts(self.volume)
        self.assertEqual(before.model_number,'MC297')
        self.assertEqual((before.hardware_serial,before.firewire_guid),('',''))
        (self.device/'SysInfoExtended').write_text('ModelNumStr: MC297\nModelNumStr: MA446\n')
        conflict=devices.device_facts(self.volume)
        self.assertTrue(conflict.identity_conflict)
        self.assertEqual((conflict.model_number,conflict.hardware_serial,conflict.firmware_version),('','',''))
        self.finder.assert_not_called()
        with mock.patch.object(devices,'_stock_identity_for_volume',side_effect=AssertionError('SysInfo read')), \
                mock.patch.object(devices,'_ioreg_records',side_effect=AssertionError('Registry read')):
            cached=devices.device_facts(self.volume,cached_only=True)
        self.assertEqual(cached,conflict)
        self.assertTrue(cached.identity_conflict)
        self.sysinfo(ModelNumStr='MC297')
        recovered=devices.device_facts(self.volume)
        self.assertFalse(recovered.identity_conflict)
        self.assertEqual(recovered.model_number,'MC297')

    def test_cached_only_fact_read_never_touches_layout_metadata_preferences_or_registry(self):
        before=devices.device_facts(self.volume)
        with mock.patch.object(Path,'is_dir',side_effect=AssertionError('layout probe')), \
                mock.patch.object(devices,'_stock_identity_for_volume',side_effect=AssertionError('SysInfo read')), \
                mock.patch.object(devices,'_classic_ipod_preferences',side_effect=AssertionError('Finder read')), \
                mock.patch.object(devices,'_ioreg_records',side_effect=AssertionError('Registry read')):
            self.assertEqual(devices.device_facts(self.volume,cached_only=True),before)

    def test_stale_cached_observation_does_not_cross_same_name_bsd_or_uuid_change(self):
        self.sysinfo(ModelNumStr='MC297',SerialNumber=SERIAL)
        self.assertEqual(devices.device_facts(self.volume).model_number,'MC297')
        devices._INFO_CACHE[str(self.volume)]=self.info(bsd='disk9s1',uuid='replacement')
        stale=devices.device_facts(self.volume,cached_only=True)
        self.assertEqual((stale.model_number,stale.hardware_serial,stale.firewire_guid),('','',''))
        self.assertFalse(stale.identity_conflict)

    def test_ordinary_generic_usb_and_missing_bsd_never_start_identity_queries(self):
        generic=self.root/'USB';generic.mkdir()
        devices._INFO_CACHE[str(generic)]=self.info()
        self.assertEqual(devices.device_facts(generic).usb_serial,'')
        self.probe.assert_not_called();self.finder.assert_not_called()
        devices._INFO_CACHE[str(self.volume)]={}
        with mock.patch.object(devices,'_stock_identity_for_volume',side_effect=AssertionError('Unbound metadata read')):
            facts=devices._classic_ipod_facts(str(self.volume))
        self.assertEqual(facts['firewire_guid'],'')
        self.probe.assert_not_called();self.finder.assert_not_called()

    def test_lowercase_exact_guid_and_invalid_finder_field_types_are_safe(self):
        self.probe.return_value=[usb(GUID.lower())]
        self.assertEqual(devices.device_facts(self.volume).hardware_serial,SERIAL)
        self.write_preferences({GUID:{'Device Class':'iPod','Serial Number':[],
                                      'Firmware Version String':{'version':'2.0.4'}}})
        fresh=devices.device_facts(self.volume)
        self.assertEqual((fresh.hardware_serial,fresh.firmware_version),('',''))

    def test_duplicate_normalized_finder_ids_refuse_even_with_a_matching_etched_serial(self):
        identifier='000A27000000ABCD'
        self.probe.return_value=[usb(identifier)]
        self.sysinfo(SerialNumber=SERIAL)
        self.write_preferences({identifier:self.record(),identifier.lower():self.record(OTHER_SERIAL,'1.3')})
        facts=devices.device_facts(self.volume)
        self.assertEqual(facts.hardware_serial,SERIAL)
        self.assertEqual(facts.serial_source,'SysInfoExtended')
        self.assertEqual(facts.firmware_version,'')
        self.assertEqual(facts.firewire_guid,identifier)

    def usb_observation(self, *, serial=SERIAL,firmware='2.0.4',model='',conflict=False):
        return StockIdentityObservation(model,'USB extended SysInfo' if model else '',
            serial,'USB extended SysInfo' if serial else '',firmware,'USB extended SysInfo' if firmware else '',conflict)

    def test_missing_passive_facts_use_exact_usb_context_once_and_cache_serial_derived_model(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation()
        first=devices.device_facts(self.volume);second=devices.device_facts(self.volume)
        self.assertEqual((first.hardware_serial,first.firmware_version),(SERIAL,'2.0.4'))
        self.assertEqual((first.serial_source,first.firmware_source),('USB extended SysInfo',)*2)
        self.assertEqual((first.model_number,first.firewire_guid),('',GUID))
        self.assertEqual(second,first);self.assertEqual(self.usb_identity.call_count,1)
        self.usb_identity.assert_called_once_with(str(self.volume),device_identifier='disk7s1',
            volume_uuid='generated-A',usb_serial=GUID,usb_registry_id=101,usb_product_id=0x1261)

    def test_complete_sysinfo_or_exact_finder_does_not_issue_usb_request(self):
        devices.device_facts(self.volume);self.usb_identity.assert_not_called()
        self.write_preferences({});self.sysinfo(ModelNumStr='MC297',SerialNumber=SERIAL,FirmwareVersion='2.0.4')
        devices.device_facts(self.volume);self.usb_identity.assert_not_called()

    def test_ambiguous_finder_serial_or_unknown_explicit_model_does_not_suppress_usb_fallback(self):
        for model in ('','MC999'):
            with self.subTest(model=model):
                devices._USB_IDENTITY_CACHE.clear();devices._USB_IDENTITY_RETRY.clear()
                self.write_preferences({GUID:self.record('ZZ123456WEE','1.3')})
                self.sysinfo(**({'ModelNumStr':model} if model else {}))
                self.usb_identity.reset_mock();self.usb_identity.return_value=self.usb_observation(model='MA446')
                devices.device_facts(self.volume)
                self.usb_identity.assert_called_once()

    def test_missing_usb_reply_backs_off_then_retries_without_caching_empty_observation(self):
        self.write_preferences({})
        with mock.patch.object(devices.time,'monotonic',return_value=0) as now:
            first=devices.device_facts(self.volume)
            self.assertEqual(first.hardware_serial,'');self.assertEqual(self.usb_identity.call_count,1)
            now.return_value=14.9;devices.device_facts(self.volume)
            self.assertEqual(self.usb_identity.call_count,1)
            self.usb_identity.return_value=self.usb_observation();now.return_value=15.1
            recovered=devices.device_facts(self.volume)
            self.assertEqual(recovered.hardware_serial,SERIAL);self.assertEqual(self.usb_identity.call_count,2)
            now.return_value=10000;devices.device_facts(self.volume)
            self.assertEqual(self.usb_identity.call_count,2)

    def test_incomplete_usb_replies_are_not_positive_cache_entries_and_retry_is_bounded(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation(firmware='')
        with mock.patch.object(devices.time,'monotonic',return_value=0) as now:
            for at,before,next_at in ((0,0,15),(15,1,45),(45,2,105),(105,3,225),(225,4,345)):
                now.return_value=at;devices.device_facts(self.volume)
                self.assertEqual(self.usb_identity.call_count,before+1)
                self.assertNotIn(str(self.volume),devices._USB_IDENTITY_CACHE)
                self.assertEqual(devices._USB_IDENTITY_RETRY[str(self.volume)][1],next_at)
                now.return_value=next_at-.01;devices.device_facts(self.volume)
                self.assertEqual(self.usb_identity.call_count,before+1)

    def test_cached_only_complete_usb_observation_never_probes_any_boundary(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation()
        before=devices.device_facts(self.volume)
        with mock.patch.object(devices,'_root_identity',side_effect=AssertionError('root stat')), \
                mock.patch.object(devices,'_stock_identity_for_volume',side_effect=AssertionError('SysInfo read')), \
                mock.patch.object(devices,'_ioreg_records',side_effect=AssertionError('Registry read')), \
                mock.patch.object(devices,'_usb_identity_for_volume',side_effect=AssertionError('USB request')):
            self.assertEqual(devices.device_facts(self.volume,cached_only=True),before)

    def test_mount_refresh_and_new_registry_never_reuse_the_previous_usb_observation(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation()
        self.assertEqual(devices.device_facts(self.volume).hardware_serial,SERIAL)
        devices.forget_device_mount(self.volume)
        devices._INFO_CACHE[str(self.volume)]=self.info(bsd='disk9s1',uuid='generated-B')
        self.probe.return_value=[usb(OTHER_GUID,bsd='disk9s1',product=0x1209,entry=202)]
        self.usb_identity.return_value=self.usb_observation(serial=OTHER_SERIAL,firmware='1.3')
        fresh=devices.device_facts(self.volume)
        self.assertEqual((fresh.hardware_serial,fresh.firmware_version,fresh.firewire_guid),(OTHER_SERIAL,'1.3',OTHER_GUID))
        self.assertEqual(self.usb_identity.call_count,2)
        self.assertEqual(self.usb_identity.call_args.kwargs['usb_registry_id'],202)

    def test_same_name_root_replacement_and_fresh_registry_context_invalidate_usb_cache(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation()
        devices.device_facts(self.volume)
        self.volume.rename(self.root/'Old');(self.volume/'iPod_Control/Device').mkdir(parents=True)
        self.probe.return_value=[usb(entry=303)]
        self.usb_identity.return_value=None
        fresh=devices.device_facts(self.volume)
        self.assertEqual((fresh.hardware_serial,fresh.firmware_version),('',''))
        self.assertEqual(self.usb_identity.call_count,2)
        self.assertEqual(self.usb_identity.call_args.kwargs['usb_registry_id'],303)

    def test_fresh_sysinfo_conflict_never_borrows_cached_usb_or_calls_endpoint(self):
        self.write_preferences({});self.usb_identity.return_value=self.usb_observation()
        devices.device_facts(self.volume)
        (self.device/'SysInfoExtended').write_text('ModelNumStr: MC297\nModelNumStr: MA446\n')
        fresh=devices.device_facts(self.volume)
        self.assertTrue(fresh.identity_conflict)
        self.assertEqual((fresh.model_number,fresh.hardware_serial,fresh.firmware_version),('','',''))
        self.assertEqual(self.usb_identity.call_count,1)
        self.assertTrue(devices.device_facts(self.volume,cached_only=True).identity_conflict)

    def test_usb_and_fresh_sysinfo_serial_conflict_remains_explicit(self):
        self.write_preferences({});self.sysinfo(SerialNumber=OTHER_SERIAL)
        self.usb_identity.return_value=self.usb_observation()
        facts=devices.device_facts(self.volume)
        self.assertTrue(facts.identity_conflict)
        self.assertEqual(facts.hardware_serial,OTHER_SERIAL)
        self.assertEqual(facts.firewire_guid,GUID)

    def test_missing_usb_ancestry_cannot_mint_signing_identity_from_a_reply(self):
        self.write_preferences({});self.probe.return_value=[]
        self.usb_identity.return_value=self.usb_observation()
        facts=devices.device_facts(self.volume)
        self.assertEqual((facts.hardware_serial,facts.firewire_guid),('',''))
        self.usb_identity.assert_not_called()

    def test_usb_result_during_mount_retirement_is_not_published_or_cached(self):
        self.write_preferences({})
        def retire(*args,**kwargs):
            devices.forget_device_mount(self.volume);return self.usb_observation()
        self.usb_identity.side_effect=retire
        facts=devices.device_facts(self.volume)
        self.assertEqual(facts.hardware_serial,'')
        self.assertNotIn(str(self.volume),devices._USB_IDENTITY_CACHE)

    def test_held_old_usb_result_cannot_replace_new_same_name_same_bsd_uuid_facts(self):
        self.write_preferences({});entered=threading.Event();release=threading.Event();old=[]
        def response(*args,**context):
            if context['usb_serial']==GUID:
                entered.set()
                if not release.wait(3):raise RuntimeError('Generated held helper timed out')
                return self.usb_observation()
            return self.usb_observation(serial=OTHER_SERIAL,firmware='1.3')
        self.usb_identity.side_effect=response
        worker=threading.Thread(target=lambda:old.append(devices.device_facts(self.volume)))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            devices.forget_device_mount(self.volume)
            self.volume.rename(self.root/'Previous');(self.volume/'iPod_Control/Device').mkdir(parents=True)
            devices._INFO_CACHE[str(self.volume)]=self.info()
            self.probe.return_value=[usb(OTHER_GUID,entry=202)]
            current=devices.device_facts(self.volume)
            self.assertEqual((current.hardware_serial,current.firewire_guid),(OTHER_SERIAL,OTHER_GUID))
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(old[0].firewire_guid,'')
        self.assertEqual(devices.device_facts(self.volume,cached_only=True),current)


if __name__ == '__main__':unittest.main()

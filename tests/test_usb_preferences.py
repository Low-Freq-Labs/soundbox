"""Offline preference/state/format checks; no mounted devices or user state."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from soundbox import my_settings,usb_format,track_info
from research.cdj_export.mysettings_codec import patch as reference_patch
from research.cdj_export.mysettings_codec import catalogue as reference_catalogue


class MySettingsProductionTests(unittest.TestCase):
    def test_every_choice_matches_the_staged_codec_and_preserves_unknown_bytes(self):
        for name,raw in my_settings.new_files({}).items():
            view=my_settings.inspect(name,raw)
            for key,field in view['fields'].items():
                for choice in field['choices']:
                    changes={key:choice}
                    candidate=my_settings.patch(name,raw,changes,expected_sha256=view['sha256'])
                    if choice in reference_catalogue()['formats'][name]['fields'].get(key,{}).get('values',{}):
                        self.assertEqual(candidate,reference_patch(name,raw,changes,expected_sha256=view['sha256']))
                    self.assertEqual(my_settings.inspect(name,candidate)['fields'][key]['value'],choice)

    def test_global_values_are_logical_and_apply_to_each_usb_own_bytes(self):
        files=my_settings.new_files({})
        profile=my_settings.values(files.items())
        profile['MYSETTING.DAT']['tempo_range']='wide'
        raw=bytearray(files['MYSETTING.DAT']);raw[142]=0x3a
        import binascii,struct
        struct.pack_into('<H',raw,144,binascii.crc_hqx(raw[104:144],0))
        raw=bytes(raw)
        patched=my_settings.patch('MYSETTING.DAT',raw,profile['MYSETTING.DAT'],expected_sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual(patched[142],0x3a)
        self.assertEqual(my_settings.values([('PIONEER/MYSETTING.DAT',patched)])['MYSETTING.DAT']['tempo_range'],'wide')
        self.assertEqual(my_settings.values([('MYSETTING.DAT',b'unknown format')]),{})

    def test_native_mic_echo_one_two_roundtrip_changes_only_two_bytes_and_crc(self):
        raw=my_settings.new_files({})['DJMMYSETTING.DAT']
        self.assertEqual(my_settings.inspect('DJMMYSETTING.DAT',raw)['fields']['mic_echo_beats']['value'],'1')
        candidate=my_settings.patch('DJMMYSETTING.DAT',raw,{'mic_echo_beats':'2'},expected_sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual(candidate[132:134],bytes([130,129]))
        self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(raw,candidate)) if i not in (132,133,156,157)))
        back=my_settings.patch('DJMMYSETTING.DAT',candidate,{'mic_echo_beats':'1'},expected_sha256=hashlib.sha256(candidate).hexdigest())
        self.assertEqual(back[132:134],bytes([129,129]))

    def test_unknown_profile_fields_and_choices_are_rejected(self):
        for profile in ({'OTHER':{}},{'MYSETTING.DAT':{'tempo_range':'guess'}},{'MYSETTING.DAT':{'guessed_byte':'on'}}):
            with self.assertRaises(ValueError):my_settings.new_files(profile)

    def test_new_talkover_choice_preserves_intervening_mixer_fields_and_restores_finite(self):
        raw=my_settings.new_files({})['DJMMYSETTING.DAT']
        candidate=my_settings.patch('DJMMYSETTING.DAT',raw,{'talk_over_level':'minus_infinity'},expected_sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual((candidate[123],candidate[131]),(0,132))
        self.assertEqual(candidate[124:131],raw[124:131])
        restored=my_settings.patch('DJMMYSETTING.DAT',candidate,{'talk_over_level':'minus_18db'},expected_sha256=hashlib.sha256(candidate).hexdigest())
        self.assertEqual(restored,raw)


class UsbFormatTests(unittest.TestCase):
    def test_supported_names_and_formats(self):
        self.assertEqual(usb_format.name_for('My USB','FAT32'),'MY USB')
        self.assertEqual(usb_format.name_for('My USB','ExFAT'),'My USB')
        for name,filesystem in (('too long for FAT32','FAT32'),('../source','ExFAT'),('USB','APFS'),('USB\0','HFS+')):
            with self.assertRaises(ValueError):usb_format.name_for(name,filesystem)

    def test_local_folders_cannot_be_formatted_and_do_not_probe_disks(self):
        with tempfile.TemporaryDirectory() as area,mock.patch('soundbox.usb_format._plist') as plist:
            with self.assertRaises(ValueError):usb_format.capture(area,token=Path(area).stat().st_dev,volume_uuid='fake')
            plist.assert_not_called()

    def test_format_rechecks_exact_disk_and_uses_supported_filesystem_and_mbr(self):
        plan=usb_format.FormatTarget('/Volumes/NOT-A-LIVE-USB',(91,92),'fake-volume','disk99',('disk99',256),256,('/Volumes/NOT-A-LIVE-USB',))
        listing={'AllDisksAndPartitions':[{'Partitions':[{'VolumeName':'MY USB','MountPoint':'/Volumes/MY USB','DeviceIdentifier':'disk99s1'}]}]}
        info={'MountPoint':'/Volumes/MY USB','VolumeUUID':'new-id','Internal':False}
        with mock.patch('soundbox.usb_format.capture',return_value=plan) as capture, \
             mock.patch('soundbox.usb_format.subprocess.run',return_value=mock.Mock(returncode=0)) as run, \
             mock.patch('soundbox.usb_format._plist',side_effect=[listing,info]):
            self.assertEqual(usb_format.format_usb(plan,'My USB','FAT32'),info)
            capture.assert_called_once()
            self.assertEqual(run.call_args.args[0],['/usr/sbin/diskutil','eraseDisk','FAT32','MY USB','MBRFormat','disk99'])
        with mock.patch('soundbox.usb_format.capture',return_value=None),mock.patch('soundbox.usb_format.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'changed'):usb_format.format_usb(plan,'USB','FAT32')
            run.assert_not_called()


class TrackInfoTests(unittest.TestCase):
    def test_export_overrides_change_only_supported_metadata(self):
        dto={'title':'Title','bpm_x100':14000,'metadata':{'play_count':1,'hot_cue_auto_load':False}}
        track_info.exported(dto,{'album':'Album','comments':'New comment','rating':'5','playCount':'3','hotCueAutoLoad':'On'})
        self.assertEqual(dto['metadata'],{'play_count':3,'hot_cue_auto_load':True})
        self.assertEqual((dto['album'],dto['comment'],dto['rating']),('Album','New comment',5))
        self.assertEqual(dto['bpm_x100'],14000)
        with self.assertRaises(ValueError):track_info.validate({'rating':'6'})
        with self.assertRaises(ValueError):track_info.validate({'bpm':'145'})
        with self.assertRaises(ValueError):track_info.validate({'releaseDate':'2026-99-99'})


class PreferenceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_usb_profile_save_emits_only_edited_fields_and_keeps_a_draft_on_refresh(self):
        from desktop.usb_preferences import MySettingsEditor
        profile=my_settings.values(my_settings.new_files({}).items())
        editor=MySettingsEditor();editor.set_profile(profile,profile)
        self.addCleanup(editor.close)
        control=editor.controls['MYSETTING.DAT','tempo_range'];control.setCurrentIndex(control.findData('wide'))
        self.assertTrue(editor.save.isEnabled());self.assertFalse(editor.promote.isEnabled())
        edits=[];editor.save_requested.connect(edits.append);editor.save.click()
        self.assertEqual(edits,[{'MYSETTING.DAT':{'tempo_range':'wide'}}])
        from copy import deepcopy
        fresh=deepcopy(profile);fresh['MYSETTING.DAT']['on_air_display']='off'
        editor.set_profile(fresh,profile,preserve_edits=True)
        self.assertEqual(editor.current()['MYSETTING.DAT']['tempo_range'],'wide')
        self.assertEqual(editor.current()['MYSETTING.DAT']['on_air_display'],'off')

    def test_category_mandatory_items_and_color_delta(self):
        from desktop.usb_preferences import OrderedChoices,BrowsePreferences
        view=OrderedChoices('categories',(4,))
        self.addCleanup(view.close)
        slots=[(4,4,'TRACK',1,True),(1,1,'GENRE',0,False)]
        view.set_slots(slots);view.active.setCurrentRow(0);view._move(view.active,view.inactive)
        self.assertEqual(view.order(),[4])
        view.inactive.setCurrentRow(0);view._move(view.inactive,view.active)
        self.assertEqual(view.order(),[4,1]);view.set_slots(slots)
        self.assertEqual(view.order(),[4,1])  # An unchanged poll cannot erase a draft.
        colors=BrowsePreferences('colors');self.addCleanup(colors.close)
        colors.set_preferences({'colors':[(i,str(i)) for i in range(1,9)]})
        colors.controls['8'].setText('Energy')
        self.assertEqual(colors.changes(),{'colors':{'8':'Energy'}})

    def test_info_has_two_tabs_and_artwork_inside_info_with_human_color_choices(self):
        from desktop.track_info import TrackInfoDialog
        from PySide6.QtWidgets import QTabWidget,QPushButton
        saved=[];pick=mock.Mock()
        dialog=TrackInfoDialog(dict(title='Title',artist='Artist',color='0'),save=lambda changes:saved.append(changes) or True,pick_art=pick)
        self.addCleanup(dialog.close)
        tabs=dialog.findChild(QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],['Summary','Info'])
        self.assertFalse(dialog.controls)
        tabs.setCurrentIndex(1)
        button=next(b for b in tabs.widget(1).findChildren(QPushButton) if b.text()=='Change artwork…')
        button.click();pick.assert_called_once()
        control=dialog.controls['color'];control.setCurrentIndex(control.findData('8'))
        dialog._save();self.assertEqual(saved,[{'color':'8'}])

    def test_info_opens_before_artwork_resolution_and_drains_its_worker(self):
        import threading
        from PySide6.QtTest import QTest
        from desktop.track_info import TrackInfoDialog
        started=threading.Event();release=threading.Event()
        def resolve():
            started.set();release.wait(2);return None
        dialog=TrackInfoDialog({'title':'Title'},artwork=resolve)
        self.addCleanup(dialog.close)
        self.assertFalse(started.is_set())
        self.assertFalse(dialog.controls)
        try:
            dialog.show()
            for _ in range(50):
                QTest.qWait(10)
                if started.is_set():break
            self.assertTrue(started.is_set())
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog._owner.pending,1)
        finally:release.set()
        dialog.reject()
        self.assertEqual(dialog._owner.pending,0)

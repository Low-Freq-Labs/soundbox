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
    @staticmethod
    def format_fixture():
        plan=usb_format.FormatTarget('/Volumes/NOT-A-LIVE-USB',(91,92),'fake-volume','disk99',
            ('disk99',256,'synthetic-usb','Synthetic USB','old-map-id'),256,('/Volumes/NOT-A-LIVE-USB',))
        listing={'AllDisksAndPartitions':[{'DeviceIdentifier':'disk99','Content':'FDisk_partition_scheme',
            'Partitions':[{'VolumeName':'MY USB','MountPoint':'/Volumes/MY USB','DeviceIdentifier':'disk99s1'}]}]}
        whole=dict(DeviceIdentifier='disk99',TotalSize=256,IORegistryEntryName='synthetic-usb',MediaName='Synthetic USB',
            Internal=False,VirtualOrPhysical='Physical',BusProtocol='USB')
        info=dict(MountPoint='/Volumes/MY USB',VolumeName='MY USB',VolumeUUID='new-id',Internal=False,
            DeviceIdentifier='disk99s1',ParentWholeDisk='disk99',FilesystemType='msdos')
        return plan,listing,whole,info

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
        for filesystem,final_name,native_fs in (('FAT32','MY USB','msdos'),('ExFAT','My USB','exfat')):
            with self.subTest(filesystem=filesystem):
                plan,listing,whole,info=self.format_fixture()
                info.update(VolumeName=final_name,FilesystemType=native_fs)
                listing['AllDisksAndPartitions'][0]['Partitions'][0]['VolumeName']=final_name
                with mock.patch('soundbox.usb_format.capture',return_value=plan) as capture, \
                     mock.patch('soundbox.usb_format._erase') as run, \
                     mock.patch('soundbox.usb_format._plist',side_effect=[listing,whole,info]):
                    self.assertEqual(usb_format.format_usb(plan,'My USB',filesystem),info)
                    capture.assert_called_once()
                    self.assertEqual(run.call_args.args[0],['/usr/sbin/diskutil','eraseDisk',filesystem,final_name,'MBRFormat','disk99'])
        with mock.patch('soundbox.usb_format.capture',return_value=None),mock.patch('soundbox.usb_format._erase') as run:
            with self.assertRaisesRegex(ValueError,'changed'):usb_format.format_usb(plan,'USB','FAT32')
            run.assert_not_called()

    def test_full_size_single_mbr_partition_can_be_quick_erased(self):
        from copy import deepcopy
        from types import SimpleNamespace
        plan,listing,whole,info=self.format_fixture()
        target=plan.target
        whole.update(TotalSize=64*1024*1024,DiskUUID='old-map-id')
        info.update(MountPoint=target,VolumeUUID=plan.volume_uuid)
        partition=listing['AllDisksAndPartitions'][0]['Partitions'][0]
        partition.update(MountPoint=target,Size=whole['TotalSize']-1024*1024,Content='DOS_FAT_32')
        for layout,expected in (('full','disk99s1'),('small',''),('gpt',''),('second',''),('wrong-type','')):
            with self.subTest(layout=layout):
                candidate=deepcopy(listing)
                row=candidate['AllDisksAndPartitions'][0]
                if layout=='small':row['Partitions'][0]['Size']=16*1024*1024
                if layout=='gpt':row['Content']='GUID_partition_scheme'
                if layout=='second':row['Partitions'][0]['DeviceIdentifier']='disk99s2'
                if layout=='wrong-type':row['Partitions'][0]['Content']='Windows_NTFS'
                with mock.patch.object(Path,'is_mount',return_value=True), \
                     mock.patch.object(Path,'stat',return_value=SimpleNamespace(st_dev=91,st_ino=92)), \
                     mock.patch.object(Path,'exists',return_value=False), \
                     mock.patch('soundbox.usb_format.assert_target_safe'), \
                     mock.patch('soundbox.usb_format._plist',side_effect=[info,whole,candidate]):
                    captured=usb_format.capture(target,token=91,volume_uuid=plan.volume_uuid)
                self.assertEqual(captured.reusable_partition,expected)
                self.assertEqual(captured.filesystem,'msdos')

    def test_same_filesystem_reuses_verified_partition_but_format_changes_repartition(self):
        from dataclasses import replace
        for filesystem,command in (('FAT32','eraseVolume'),('ExFAT','eraseDisk')):
            with self.subTest(filesystem=filesystem):
                plan,listing,whole,info=self.format_fixture()
                plan=replace(plan,reusable_partition='disk99s1',filesystem='msdos')
                if filesystem=='ExFAT':
                    info.update(VolumeName='My USB',FilesystemType='exfat')
                    listing['AllDisksAndPartitions'][0]['Partitions'][0]['VolumeName']='My USB'
                with mock.patch('soundbox.usb_format.capture',return_value=plan), \
                     mock.patch('soundbox.usb_format._erase') as run, \
                     mock.patch('soundbox.usb_format._plist',side_effect=[listing,whole,info]):
                    usb_format.format_usb(plan,'My USB',filesystem)
                expected=['/usr/sbin/diskutil',command,filesystem,info['VolumeName']]
                expected+=['disk99s1'] if command=='eraseVolume' else ['MBRFormat','disk99']
                self.assertEqual(run.call_args.args[0],expected)

    def test_post_format_wrong_disk_map_filesystem_or_parent_prevents_initialization(self):
        from copy import deepcopy
        for area,key,value in (('map','Content','GUID_partition_scheme'),('disk','TotalSize',512),
                ('disk','BusProtocol','SATA'),('volume','FilesystemType','apfs'),('volume','ParentWholeDisk','disk98')):
            with self.subTest(area=area,key=key):
                plan,listing,whole,info=self.format_fixture()
                listing=deepcopy(listing)
                objects={'map':listing['AllDisksAndPartitions'][0],'disk':whole,'volume':info}
                objects[area][key]=value
                with mock.patch('soundbox.usb_format.capture',return_value=plan), \
                     mock.patch('soundbox.usb_format._erase'), \
                     mock.patch('soundbox.usb_format._plist',side_effect=[listing,whole,info]):
                    with self.assertRaisesRegex(RuntimeError,'USB formatted'):
                        usb_format.format_usb(plan,'My USB','FAT32')

    def test_post_format_extra_partition_cannot_place_the_requested_volume_second(self):
        plan,listing,whole,info=self.format_fixture()
        requested=listing['AllDisksAndPartitions'][0]['Partitions'][0]
        requested['DeviceIdentifier']='disk99s2';info['DeviceIdentifier']='disk99s2'
        listing['AllDisksAndPartitions'][0]['Partitions'].insert(0,{'DeviceIdentifier':'disk99s1','VolumeName':'Unexpected'})
        with mock.patch('soundbox.usb_format.capture',return_value=plan), \
             mock.patch('soundbox.usb_format._erase'), \
             mock.patch('soundbox.usb_format._plist',side_effect=[listing,whole,info]):
            with self.assertRaisesRegex(RuntimeError,'one data partition'):
                usb_format.format_usb(plan,'My USB','FAT32')

    def test_cancel_before_erase_never_starts_diskutil_or_post_format_reads(self):
        plan,*_=self.format_fixture()
        for checks,capture_calls in (([True],0),([False,True],1)):
            with self.subTest(checks=checks), \
                 mock.patch('soundbox.usb_format.capture',return_value=plan) as capture, \
                 mock.patch('soundbox.usb_format._erase') as erase, \
                 mock.patch('soundbox.usb_format._plist') as plist:
                with self.assertRaisesRegex(RuntimeError,'nothing erased'):
                    usb_format.format_usb(plan,'My USB','FAT32',should_cancel=mock.Mock(side_effect=checks))
                self.assertEqual(capture.call_count,capture_calls)
                erase.assert_not_called();plist.assert_not_called()

    def test_format_reports_native_stages_and_does_not_cancel_an_erase_in_progress(self):
        plan,listing,whole,info=self.format_fixture()
        cancelled=False
        labels=[]
        def erase(command,progress):
            nonlocal cancelled
            cancelled=True
            progress(0,0,'Unmounting disk')
            progress(0,0,'Mounting disk')
        with mock.patch('soundbox.usb_format.capture',return_value=plan), \
             mock.patch('soundbox.usb_format._erase',side_effect=erase), \
             mock.patch('soundbox.usb_format._plist',side_effect=[listing,whole,info]):
            result=usb_format.format_usb(plan,'My USB','FAT32',should_cancel=lambda:cancelled,
                                        on_progress=lambda _done,_total,label:labels.append(label))
        self.assertEqual(result,info)
        self.assertEqual(labels,['Checking USB','Erasing USB','Unmounting disk','Mounting disk','Finishing USB format'])

    def test_native_erase_streams_output_before_exit_and_closes_and_reaps_its_process(self):
        import sys
        actual=usb_format.subprocess.Popen
        children=[]
        stages=[]
        def start(*args,**kwargs):
            kwargs['stdin']=usb_format.subprocess.PIPE
            process=actual(*args,**kwargs);children.append(process);return process
        def progress(_done,_total,label):
            stages.append((label,children[0].poll()))
            if label=='Unmounting disk':
                children[0].stdin.write(b'y');children[0].stdin.flush()
        code="import sys; print('Unmounting disk',flush=True); sys.stdin.read(1); print('Mounting disk',flush=True)"
        with mock.patch('soundbox.usb_format.subprocess.Popen',side_effect=start):
            try:usb_format._erase([sys.executable,'-c',code],progress)
            finally:children[0].stdin.close()
        self.assertEqual([s[0] for s in stages],['Unmounting disk','Mounting disk'])
        self.assertIsNone(stages[0][1])
        self.assertEqual(children[0].returncode,0)
        self.assertTrue(children[0].stdout.closed)

    def test_native_erase_bounds_failed_output_and_survives_a_failed_progress_consumer(self):
        import sys
        actual=usb_format.subprocess.Popen
        children=[]
        def start(*args,**kwargs):
            process=actual(*args,**kwargs);children.append(process);return process
        code="import os,sys; os.write(1,b'x'*200000); os.write(2,b'\\nNative erase refused\\n'); sys.exit(7)"
        with mock.patch('soundbox.usb_format.subprocess.Popen',side_effect=start), \
             mock.patch('soundbox.usb_format._LOG.exception') as logged:
            with self.assertRaisesRegex(RuntimeError,'Native erase refused') as error:
                usb_format._erase([sys.executable,'-c',code],mock.Mock(side_effect=RuntimeError('closed view')))
        self.assertLessEqual(len(str(error.exception)),2000)
        self.assertEqual(children[0].returncode,7)
        self.assertTrue(children[0].stdout.closed)
        logged.assert_called()

    def test_native_erase_raw_read_failure_drains_a_verbose_child_before_reaping(self):
        import sys
        actual_start=usb_format.subprocess.Popen
        actual_read=usb_format.os.read
        children=[]
        failed=False
        def start(*args,**kwargs):
            process=actual_start(*args,**kwargs);children.append(process);return process
        def read(fd,size):
            nonlocal failed
            if children and fd==children[0].stdout.fileno() and not failed:
                failed=True
                raise OSError('simulated native-output read failure')
            return actual_read(fd,size)
        code="import os; os.write(1,b'x'*200000)"
        with mock.patch('soundbox.usb_format.subprocess.Popen',side_effect=start), \
             mock.patch('soundbox.usb_format.os.read',side_effect=read):
            with self.assertRaisesRegex(OSError,'simulated native-output read failure'):
                usb_format._erase([sys.executable,'-c',code],lambda *_:None)
        self.assertTrue(failed)
        self.assertEqual(children[0].returncode,0)
        self.assertTrue(children[0].stdout.closed)


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
        self.assertIn('color',dialog.controls)
        controls=dict(dialog.controls)
        dialog.prepare_artwork();self.assertTrue(dialog._art_ready)
        tabs.setCurrentIndex(1)
        self.assertEqual(dialog.controls,controls)
        button=next(b for b in tabs.widget(1).findChildren(QPushButton) if b.text()=='Change artwork…')
        button.click();pick.assert_called_once()
        control=dialog.controls['color'];control.setCurrentIndex(control.findData('8'))
        dialog._save();self.assertEqual(saved,[{'color':'8'}])

    def test_info_prepares_artwork_before_show_and_drains_its_worker(self):
        import threading
        from PySide6.QtCore import QTimer
        from desktop.track_info import TrackInfoDialog
        started=threading.Event();release=threading.Event()
        def resolve():
            started.set();release.wait(2);return None
        dialog=TrackInfoDialog({'title':'Title'},artwork=resolve)
        self.addCleanup(dialog.close)
        self.assertFalse(started.is_set())
        self.assertFalse(dialog.controls)  # Read-only dialog has no editable controls.
        from PySide6.QtWidgets import QLineEdit
        self.assertTrue(dialog.tabs.widget(1).findChildren(QLineEdit))
        observed=[]
        def release_after_worker_starts():
            if not started.is_set():
                QTimer.singleShot(1,release_after_worker_starts);return
            observed.append((dialog.isVisible(),dialog._owner.pending))
            release.set()
        QTimer.singleShot(1,release_after_worker_starts)
        try:
            dialog.prepare_artwork()
            self.assertEqual(observed,[(False,1)])
            self.assertTrue(dialog._art_ready)
            self.assertEqual(dialog._owner.pending,0)
            dialog.show()
            self.assertTrue(dialog.isVisible())
        finally:release.set()
        dialog.reject()
        self.assertEqual(dialog._owner.pending,0)

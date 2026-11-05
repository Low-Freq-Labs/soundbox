"""Native CDJ planning/review/publication inside the shared device worker lane."""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import threading

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from .devices import device_facts


def _source_edits_pending(host):
    controller=getattr(host,'playlist_controller',None)
    imports=getattr(host,'shared_import_controller',None)
    analysis=getattr(host,'native_analysis_controller',None)
    prep=getattr(host,'prep_controller',None)
    media=getattr(host,'media_controller',None)
    return bool(host.tag_controller.owned or controller and
                (controller.pending or controller.queue_error()) or imports and
                (imports.pending or imports.queue_error()) or analysis and analysis.pending or prep and prep.pending
                or media and (media.scan_again or media.tasks.pending or any(not j['error'] for j in media.pending)))


class CdjWorker(QThread):
    progress = Signal(int, int, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.result = None
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def run(self):
        try:
            self.result = self.operation(self.stop.is_set, self.progress.emit)
        except Exception as exc:
            self.result = exc


class CdjDeviceMixin:
    def _begin_cdj_export(self, dev, playlists=(), *, settings=None, from_drop=False, drop_target=None,
                          standalone=()):
        from soundbox import cdj_export
        if self._shutting_down or self.lib is None:
            return
        if from_drop and drop_target is None:
            drop_target = (self._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid)
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_cdj_export(dev, playlists, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone),target=dev):
            return
        if self._device_operation_owned(dev):
            QMessageBox.information(self, 'Device busy', 'Wait for the current device operation.')
            return
        # Accepted source edits drain before freezing a generation; a timer keeps
        # Qt delivery and the native player alive while that FIFO finishes.
        if _source_edits_pending(self):
            native_ids={p.pid for p in getattr(self.lib,'playlists',())}
            selected_native={getattr(p,'pid',None) for p in playlists} & native_ids
            source_binding=(str(self.lib.source),self.lib.source_library_id) if hasattr(self.lib,'source') else None
            request = (dev, tuple(playlists), settings, from_drop, drop_target)
            self._cdj_waiting = request
            self._start_pulse('Waiting for source edits')
            self.cancel_btn.setVisible(True)
            def ready():
                if self._shutting_down or self._cdj_waiting is not request:
                    return
                if _source_edits_pending(self):
                    QTimer.singleShot(100, ready)
                    return
                self._cdj_waiting = None
                selected=playlists
                fresh={p.pid:p for p in getattr(self.lib,'playlists',())}
                if (source_binding is not None and source_binding!=(str(self.lib.source),self.lib.source_library_id)
                        or selected_native-fresh.keys()):
                    self._stop_pulse();self.cancel_btn.setVisible(False)
                    self._flash_status('Library or selected playlist changed; choose the playlist again',15)
                    if getattr(self,'_export_batch',None):
                        self._export_target_result(dev,RuntimeError('Library or selected playlist changed'))
                        self._maybe_finish_export_target()
                    return
                if selected_native:
                    selected=[fresh[p.pid] if p.pid in selected_native else p for p in playlists]
                self._begin_cdj_export(dev, selected, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone)
                if getattr(self,'_export_batch',None):self._maybe_finish_export_target()
            QTimer.singleShot(100, ready)
            return
        profile = self._device_profiles.get(dev)
        facts = device_facts(dev, cached_only=True)
        token = self._device_mount_tokens.get(dev, 0)
        if from_drop and drop_target != (token,facts.volume_uuid):
            self._flash_status('USB changed; choose the connected target again' if standalone else
                               'USB changed after the drop; drop the playlist onto the connected device again',15)
            if getattr(self,'_export_batch',None):self._export_target_result(dev,RuntimeError('USB changed; select the connected device again'))
            return
        if not profile or profile.kind != 'generic_usb' or not getattr(profile,'cdj_export',profile.rockbox_export) or not facts.writable or not facts.volume_uuid:
            QMessageBox.warning(self, 'CDJ export unavailable', 'Select a writable FAT32 or exFAT USB.')
            return
        self._release_eject_ready()
        self._exporting = True
        self._cdj_plan = None
        recovery = self._data_file.parent/'cdj-recovery'
        if settings is None:
            # Freeze curation and selection before the worker sees them.
            library, titles, artists = deepcopy(self.lib), dict(self.titles), dict(self.overrides)
            playlists = deepcopy(tuple(playlists))
            standalone = deepcopy(tuple(standalone))
            # Additive publication touches only this explicit selection.
            all_playlists = ()
            global_profile = deepcopy(self.settings.get('cdj_my_settings',{}))
            operation = lambda cancel, progress: cdj_export.plan_export(
                library, playlists, dev, token=token, volume_uuid=facts.volume_uuid,
                all_playlists=all_playlists, titles=titles, artists=artists, art_cache=self.art_cache,
                should_cancel=cancel, on_progress=progress, recovery_root=recovery, additive=True,
                global_profile=global_profile,standalone=standalone)
        else:
            changes = dict(settings)
            # Settings need source separation, not detached song/cue/catalog
            # data. Capture those paths now; later library edits cannot alter
            # the worker's original overlap boundary.
            sources = [Path(self.lib.source), *[t.path for t in self.lib.tracks_by_id.values()],
                       *[Path(t.source_analysis_path) for t in self.lib.tracks_by_id.values() if t.source_analysis_path]]
            operation = lambda cancel, _progress: cdj_export.plan_settings(
                dev, changes, token=token, volume_uuid=facts.volume_uuid,
                sources=sources,
                should_cancel=cancel, recovery_root=recovery)
        worker = CdjWorker(operation)
        self._cdj_worker = worker
        worker.progress.connect(lambda done, total, label: self._set_device_progress(
            round(650*done/max(1, total))))
        worker.finished.connect(lambda w=worker: self._finish_device_result_worker(
            '_cdj_worker', w, lambda result: self._review_cdj_plan(dev, result, w.stop.is_set(), from_drop=from_drop)))
        self._set_device_progress(0)
        self.progress.setVisible(True)
        self._start_pulse('Preparing USB settings' if settings is not None else 'Preparing CDJ export')
        self._start_device_worker(worker, dev, 'plan-cdj')
        self._sync_cue_editing()

    def _review_cdj_plan(self, dev, result, cancelled=False, *, from_drop=False):
        from soundbox import cdj_transaction
        if isinstance(result, Exception):
            self._finish_cdj(dev, result)
            return
        if cancelled:
            result.close()
            self._finish_cdj(dev, RuntimeError('CDJ preparation cancelled; no USB files were changed'))
            return
        plan = result
        if not getattr(plan,'requires_commit',bool(plan.changes)):
            plan.close()
            self._finish_cdj(dev, None, unchanged=True)
            return
        def publish(cancel, progress):
            try:
                return cdj_transaction.commit(plan, should_cancel=cancel, on_progress=progress)
            finally:
                plan.close()
        worker = CdjWorker(publish)
        self._cdj_worker = worker
        worker.progress.connect(lambda done, total, _label: self._set_device_progress(round(650+350*done/max(1,total))))
        worker.finished.connect(lambda w=worker: self._finish_device_result_worker(
            '_cdj_worker', w, lambda value: self._finish_cdj(dev, value)))
        self._start_pulse('Exporting CDJ USB')
        self._set_device_progress(650)
        self._start_device_worker(worker, dev, 'commit-cdj')

    def _finish_cdj(self, dev, result, *, cancelled=False, unchanged=False):
        self._exporting = False
        self._cancelling = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        self.progress.setVisible(False)
        self._sync_cue_editing()
        if getattr(self,'_export_batch',None):self._export_target_result(dev,result)
        if isinstance(result, Exception):
            if not getattr(self,'_export_batch',None):QMessageBox.warning(self, 'CDJ USB operation stopped', str(result))
            self._flash_status('CDJ USB operation stopped', 15)
        else:
            self._flash_status('CDJ export cancelled — nothing written' if cancelled else
                               'Selected playlists already exported' if unchanged else 'Playlist export complete ✓', 15)
        # The committed export is the result. Invalidate displayed catalog
        # rows without a final USB read, retaining the user's settings drafts.
        # Failures/unchanged exports keep their previous presentation intact.
        if not isinstance(result, Exception) and not cancelled and not unchanged:
            from dataclasses import replace
            from .usb_device_view import UsbSnapshot
            previous = self._usb_view_cache.get(dev)
            notice = 'USB updated. Select a tab to browse its contents.'
            snapshot = (replace(previous, playlists=(), error='', notice=notice)
                        if isinstance(previous, UsbSnapshot) else
                        UsbSnapshot(Path(dev), self._device_mount_tokens.get(dev, 0),
                                    device_facts(dev, cached_only=True), notice=notice))
            self._usb_view_cache[dev] = snapshot
            self._usb_view_requests.discard(dev)
            if dev == self._current_device_path:
                self._present_usb_device(dev)
        self._update_device_action_state()
        if getattr(self, '_usb_format_device', ''):
            self._show_usb_format_status()

    def _apply_usb_settings(self, changes):
        dev = self._current_device_path
        if dev:
            self._begin_cdj_export(dev, settings=changes)

    def _sync_usb_my_settings(self, snapshot):
        from soundbox import my_settings
        if getattr(snapshot, 'notice', ''):
            return
        native = snapshot.native if snapshot else None
        current = my_settings.values(native.files) if native else {}
        if current and not self.settings.get('cdj_my_settings'):
            self.settings['cdj_my_settings'] = current
            if not self._save_state(invalidate_views=False):
                self.settings.pop('cdj_my_settings',None)
                return
        profile = self.settings.get('cdj_my_settings',{})
        pages = vars(self).get('usb_device_pages')
        if pages is not None:
            pages.global_profile = deepcopy(profile)
            editor = pages.my_settings
            if editor.profile!=current or editor.global_profile!=profile:
                editor.set_profile(current,profile,preserve_edits=True)
        editor = getattr(self, '_global_my_settings', None)
        if editor is not None and editor.profile!=profile:
            editor.set_profile(profile)

    def _save_global_my_settings(self, profile):
        from soundbox import my_settings
        profile = my_settings.validate(profile)
        previous = self.settings.get('cdj_my_settings',{})
        self.settings['cdj_my_settings'] = deepcopy(profile)
        if not self._save_state(invalidate_views=False):
            self.settings['cdj_my_settings'] = previous
            return
        editor = getattr(self, '_global_my_settings', None)
        if editor is not None:
            editor.set_profile(profile)
        pages = vars(self).get('usb_device_pages')
        if pages is not None:
            pages.global_profile = deepcopy(profile)
            native = getattr(pages._snapshot,'native',None)
            pages.my_settings.set_profile(my_settings.values(native.files) if native else {},profile)
        self._flash_status('Global My Settings saved')

    def _promote_usb_settings(self, profile):
        merged = deepcopy(self.settings.get('cdj_my_settings',{}))
        for name,fields in profile.items():
            merged.setdefault(name,{}).update(fields)
        self._save_global_my_settings(merged)

    def _load_global_usb_settings(self):
        from soundbox import my_settings
        native = getattr(self.usb_device_pages._snapshot,'native',None)
        if native is None:return
        current = my_settings.values(native.files)
        changes = {name:{key:self.settings['cdj_my_settings'][name][key] for key in fields
                   if key in self.settings.get('cdj_my_settings',{}).get(name,{})}
                   for name,fields in current.items()}
        self._apply_usb_settings({'my_settings':changes})

    def _import_usb_my_settings(self, dev):
        if self._defer_device_operation_until_scan_finishes(lambda:self._import_usb_my_settings(dev)):return
        if self._device_operation_owned():return
        from soundbox import my_settings
        from soundbox.cdj_usb import read_profiles
        token=self._device_mount_tokens.get(dev,0)
        self._exporting=True;self._release_eject_ready()
        worker=CdjWorker(lambda _cancel,_progress:my_settings.values(read_profiles(dev,token)))
        self._cdj_worker=worker
        def imported(result):
            self._exporting=False;self._stop_pulse()
            if isinstance(result,Exception):self._finish_cdj(dev,result);return
            if not result:self._flash_status('No supported My Settings on this USB');return
            self._promote_usb_settings(result)
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,imported))
        self._start_pulse('Importing My Settings');self._start_device_worker(worker,dev,'inspect')

    def _reformat_usb(self, dev):
        from soundbox import cdj_export, cdj_transaction
        if self._defer_device_operation_until_scan_finishes(lambda:self._reformat_usb(dev)):return
        if self._device_operation_owned():return
        from soundbox import usb_format
        profile=self._device_profiles.get(dev)
        if profile is None or profile.kind!='generic_usb':return
        facts=device_facts(dev,cached_only=True);token=self._device_mount_tokens.get(dev,0)
        sources=[Path(self.lib.source),*[t.path for t in self.lib.tracks_by_id.values()]] if self.lib else []
        if self.lib:sources.extend(Path(t.source_analysis_path) for t in self.lib.tracks_by_id.values() if t.source_analysis_path)
        self._usb_format_device=dev
        self._usb_format_volumes=(dev,)
        self._usb_format_started=False
        self._usb_format_original_text=''
        self._release_eject_ready()
        worker=CdjWorker(lambda _cancel,_progress:usb_format.capture(dev,token=token,volume_uuid=facts.volume_uuid,sources=sources))
        def show(result):
            if isinstance(result,Exception):self._finish_usb_format(dev,result);return
            self._stop_pulse()
            self._usb_format_volumes=result.volumes
            from .usb_preferences import FormatUsbDialog
            dialog=FormatUsbDialog(result,self)
            try:
                accepted=dialog.exec()==QDialog.DialogCode.Accepted
                choice=dialog.choice if accepted else None
            finally:
                dialog.deleteLater()
            if not accepted or self._shutting_down:
                self._finish_usb_format(dev,None,cancelled=True);return
            global_profile=deepcopy(self.settings.get('cdj_my_settings',{}))
            format_name,format_filesystem=choice
            recovery=self._data_file.parent/'cdj-recovery'
            def format_and_initialize(cancel,progress):
                info=usb_format.format_usb(result,format_name,format_filesystem,sources=sources,
                    should_cancel=cancel,on_progress=progress)
                path=Path(info['MountPoint'])
                progress(0,0,'Initializing USB')
                plan=cdj_export.plan_initialize(path,token=path.stat().st_dev,volume_uuid=info['VolumeUUID'],
                    sources=sources,profile=global_profile,name=info['VolumeName'],recovery_root=recovery,
                    should_cancel=cancel)
                try:cdj_transaction.commit(plan)
                finally:plan.close()
                return str(path)
            next_worker=CdjWorker(format_and_initialize)
            self._usb_format_started=True
            self._start_usb_format_worker(next_worker,dev,
                lambda value:self._finish_usb_format(dev,value),'Formatting USB')
        self._start_usb_format_worker(worker,dev,show,'Reading USB')

    def _start_usb_format_worker(self,worker,dev,callback,label):
        """Own the erase separately so another drive can use the export lane."""
        self._usb_format_worker=worker
        worker.progress.connect(lambda _done,_total,text:
            self._usb_format_progress(dev,text) if self._usb_format_worker is worker else None)
        def finished():
            worker.wait()
            if self._usb_format_worker is not worker:return
            self._usb_format_worker=None
            worker.deleteLater()
            if self._shutting_down:
                self._usb_format_device=''
                return
            try:
                callback(worker.result)
            except Exception as exc:
                self._finish_usb_format(dev,exc)
        worker.finished.connect(finished)
        self._usb_format_progress(dev,label)
        try:
            worker.start()
        except Exception as exc:
            self._usb_format_worker=None
            worker.deleteLater()
            self._finish_usb_format(dev,exc)
            return
        self._update_device_action_state()

    def _usb_format_progress(self,dev,label):
        if dev!=getattr(self,'_usb_format_device','') or self._shutting_down:return
        native=(('Started erase','Formatting USB'),('Unmounting','Unmounting USB'),
                ('Erasing','Erasing USB'),('Mounting','Mounting USB'),
                ('Finished erase','Finishing USB format'),('Creating the partition map','Preparing USB'),
                ('Waiting for partitions','Preparing USB'),('Formatting disk','Formatting USB'))
        stages={'Reading USB','Checking USB','Formatting USB','Erasing USB',
                'Finishing USB format','Initializing USB'}
        if label not in stages:
            label=next((stage for prefix,stage in native if label.startswith(prefix)),'')
            if not label:return
        self._usb_format_label=label
        self._show_usb_format_status()

    def _show_usb_format_status(self):
        dev=getattr(self,'_usb_format_device','')
        if not dev:return
        row=self._usb_device_rows.get(dev)
        name=row[0] if row else Path(dev).name
        for at in range(self.device_tree.topLevelItemCount()):
            item=self.device_tree.topLevelItem(at)
            from .trees import DEVPATH_ROLE
            if item.data(0,DEVPATH_ROLE)==dev:
                if not self._usb_format_original_text:self._usb_format_original_text=item.text(0)
                item.setText(0,f'{name} · {self._usb_format_label}…')
        # A simultaneous export owns the footer and its Cancel control.
        if ((not getattr(self,'_usb_format_started',False) or not self._device_operation_owned(''))
                and not getattr(self,'_export_batch',None)):
            self._start_pulse(f'{self._usb_format_label} · {name}')
            self.cancel_btn.setVisible(False)

    def _finish_usb_format(self,dev,result,*,cancelled=False):
        self._usb_format_device=''
        idle=not self._device_operation_owned('') and not getattr(self,'_export_batch',None)
        if idle:self._stop_pulse()
        if isinstance(result,Exception):
            QMessageBox.warning(self,'USB format stopped',str(result))
            if idle:self._flash_status('USB format stopped',15)
        elif not cancelled and idle:
            self._flash_status('USB formatted · Global My Settings loaded',15)
        if getattr(self,'_usb_format_started',False):
            from .devices import forget_device_mount
            from .trees import DEVPATH_ROLE
            # Every old partition identity is retired, including an erase that
            # succeeded before initialization failed. Never offer stale targets
            # while another export holds discovery off.
            for volume in self._usb_format_volumes:
                forget_device_mount(volume)
                self._device_coordinator.disconnect(volume)
                for cache in (self._usb_view_cache,self._device_profiles,
                              self._device_mount_tokens,self._usb_device_rows):
                    cache.pop(volume,None)
                self._usb_view_requests.discard(volume)
                for at in range(self.device_tree.topLevelItemCount()-1,-1,-1):
                    if self.device_tree.topLevelItem(at).data(0,DEVPATH_ROLE)==volume:
                        self.device_tree.takeTopLevelItem(at)
                if self._current_device_path==volume:
                    self._current_device_path=''
                    self.stack.setCurrentIndex(0)
            self._device_mount_refresh_pending=True
        if cancelled or isinstance(result,Exception):
            row=self._usb_device_rows.get(dev)
            if row:
                from .trees import DEVPATH_ROLE
                for at in range(self.device_tree.topLevelItemCount()):
                    item=self.device_tree.topLevelItem(at)
                    if item.data(0,DEVPATH_ROLE)==dev:
                        item.setText(0,self._usb_format_original_text or row[0])
        self._update_device_action_state()
        self._sync_cue_editing()
        if getattr(self,'_device_mount_refresh_pending',False):QTimer.singleShot(0,self._refresh_devices)

    def _recover_cdj(self, dev):
        from soundbox import cdj_transaction
        if self._device_operation_owned():
            return
        if self._defer_device_operation_until_scan_finishes(lambda:self._recover_cdj(dev)):
            return
        facts = device_facts(dev,cached_only=True)
        token = self._device_mount_tokens.get(dev,0)
        root = self._data_file.parent/'cdj-recovery'
        sources = [t.path for t in self.lib.tracks_by_id.values()] if self.lib else []
        self._exporting = True
        self._release_eject_ready()
        def inspect(_cancel,_progress):
            session = cdj_transaction.CdjSession(dev,sources,token=token,volume_uuid=facts.volume_uuid)
            return cdj_transaction.recover(session,root,dry_run=True)
        worker = CdjWorker(inspect)
        self._cdj_worker = worker
        def reviewed(result):
            if isinstance(result,Exception):
                self._finish_cdj(dev,result)
                return
            if result is None:
                self._finish_cdj(dev,None,unchanged=True)
                return
            text = (f'{Path(dev).name}\n\n'
                + ('Finish the already committed generation' if result['state']=='committed'
                   else 'Restore the previous library generation')
                + f" using its verified local receipt and {result['files']} file records?")
            if worker.stop.is_set() or QMessageBox.question(self,'Recover CDJ USB',text,
                    QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                self._finish_cdj(dev,None,cancelled=True)
                return
            def apply(_cancel,_progress):
                from soundbox.cdj_usb import digest
                session = cdj_transaction.CdjSession(dev,sources,token=token,volume_uuid=facts.volume_uuid)
                if session.mount_identity != tuple(result['mount_identity']):
                    raise ValueError('USB was remounted after recovery review')
                if digest(session.read_optional(cdj_transaction.JOURNAL,limit=8*1024*1024)) != result['journal_sha256']:
                    raise ValueError('USB recovery journal changed after review')
                cdj_transaction.recover(session,root)
            current = CdjWorker(apply)
            self._cdj_worker = current
            current.finished.connect(lambda w=current:self._finish_device_result_worker(
                '_cdj_worker',w,lambda value:self._finish_cdj(dev,value)))
            self._start_pulse('Recovering CDJ USB')
            self._start_device_worker(current,dev,'commit-cdj-recovery')
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,reviewed))
        self._start_pulse('Checking CDJ recovery')
        self._start_device_worker(worker,dev,'plan-cdj-recovery')

    def _save_device_views(self):
        saved = self.settings.get('device_views',{})
        saved = dict(saved) if isinstance(saved,dict) else {}
        saved['usb'] = deepcopy(self.usb_device_pages._states)
        self.settings['device_views'] = saved
        if not self._shutting_down:
            self._schedule_settings_save()

    def _remember_stock_device_view(self, *_):
        if getattr(self,'_restoring_stock_view',False):
            return
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        if not dev or not getattr(profile,'has_stock_ipod',False):
            return
        snapshot = self._stock_snapshot
        if snapshot is None or str(snapshot.target) != dev:
            return
        from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeWidget, QTreeWidgetItemIterator
        from PySide6.QtCore import Qt
        states = {}
        for view in self.stock_ipod_pages.findChildren(QAbstractItemView):
            if not isinstance(view,(QTableView,QTreeWidget)):
                continue
            key = view.objectName()
            if key == 'StockDeviceDetails':
                key += ':'+view.headerItem().text(0)
            selected = []
            index = view.currentIndex()
            while index.isValid():
                selected.insert(0,index.row())
                index = index.parent()
            states[key] = dict(scroll=view.verticalScrollBar().value(),selected=selected)
            if isinstance(view,QTreeWidget):
                expanded = []
                iterator = QTreeWidgetItemIterator(view)
                while iterator.value():
                    item = iterator.value()
                    if item.isExpanded():
                        expanded.append(str(item.data(0,Qt.UserRole)))
                    iterator += 1
                states[key]['expanded'] = expanded
        saved = dict(self.settings.get('device_views',{}))
        stock = dict(saved.get('stock',{}))
        stock[snapshot.identity_key or dev] = dict(page=self.stock_ipod_pages.stack.currentIndex(),views=states)
        saved['stock'] = stock
        self.settings['device_views'] = saved
        if not self._shutting_down:
            self._schedule_settings_save()

    def _restore_stock_device_view(self):
        snapshot = self._stock_snapshot
        if snapshot is None:
            return
        state = self.settings.get('device_views',{}).get('stock',{}).get(snapshot.identity_key or str(snapshot.target),{})
        if not state:
            return
        from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeWidget, QTreeWidgetItemIterator
        from PySide6.QtCore import Qt, QModelIndex
        self._set_stock_device_tab(max(0,min(2,int(state.get('page',0)))))
        for view in self.stock_ipod_pages.findChildren(QAbstractItemView):
            if not isinstance(view,(QTableView,QTreeWidget)):
                continue
            key = view.objectName()
            if key == 'StockDeviceDetails':
                key += ':'+view.headerItem().text(0)
            previous = state.get('views',{}).get(key,{})
            if isinstance(view,QTreeWidget):
                iterator = QTreeWidgetItemIterator(view)
                while iterator.value():
                    item = iterator.value()
                    if str(item.data(0,Qt.UserRole)) in previous.get('expanded',[]):
                        item.setExpanded(True)
                    iterator += 1
            index = QModelIndex()
            for row in previous.get('selected',[]):
                index = view.model().index(int(row),0,index)
                if not index.isValid():
                    break
            if index.isValid():
                view.setCurrentIndex(index)
            view.verticalScrollBar().setValue(int(previous.get('scroll',0)))

    def _on_usb_column_resized(self, column, _old, width):
        if getattr(self,'_artist_column_sync',False):
            return
        self.table.setColumnWidth(column,width)

    def _enrich_usb_row(self, song, row):
        # A desktop artwork cache can change independently of the exported
        # cover. USB rows describe their native catalog reference only.
        row['art'] = 'cover' if song.artwork else ''

    def _load_usb_song(self, song):
        snapshot = self.usb_device_pages._snapshot
        if not hasattr(snapshot,'target') or snapshot.error or snapshot.notice:
            return
        dev = str(snapshot.target)
        if self._defer_device_operation_until_scan_finishes(lambda:self._load_usb_song(song)):
            return
        if self._device_operation_owned():
            self._flash_status('Wait for the current device operation')
            return
        from soundbox.cdj_usb import prepare_playback
        self._release_eject_ready()
        worker = CdjWorker(lambda _cancel,_progress:prepare_playback(snapshot.target,snapshot.mount_token,song))
        self._cdj_worker = worker
        def loaded(result):
            if worker.stop.is_set() or self._shutting_down:
                return
            if isinstance(result,Exception):
                self._flash_status(str(result),15)
                return
            audio,analysis = result
            track_id = f'usb:{snapshot.facts.volume_uuid}:{song.id}'
            self._capture_autoplay_context(track_id)
            title = f'{song.artist} — {song.title}' if song.artist else song.title
            self.player_bar.play_track(track_id,audio,title=title,bpm=f'{song.bpm/100:.2f}' if song.bpm else None,
                analysis_path=str(analysis) if analysis else '',autoplay=None,cues=None)
            self._sync_cue_editing()
            self._update_player_navigation()
            self._flash_status('Loaded from USB',5)
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,loaded))
        self._start_device_worker(worker,dev,'inspect')


    def _load_browser_selection(self):
        if self.stack.currentIndex()==1 and self.device_content_stack.currentIndex()==2:
            item = self.usb_device_pages.playlist_tree.currentItem()
            if item is not None:
                self.usb_device_pages._activate_song(item)
        else:
            self._play_row(self.table.currentIndex())

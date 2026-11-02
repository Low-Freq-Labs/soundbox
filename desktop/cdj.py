"""Native CDJ planning/review/publication inside the shared device worker lane."""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import threading

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from soundbox import cdj_export, cdj_transaction
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
        if self._shutting_down or self.lib is None:
            return
        if from_drop and drop_target is None:
            drop_target = (self._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid)
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_cdj_export(dev, playlists, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone)):
            return
        if self._device_operation_owned():
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
                    return
                if selected_native:
                    selected=[fresh[p.pid] if p.pid in selected_native else p for p in playlists]
                self._begin_cdj_export(dev, selected, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone)
            QTimer.singleShot(100, ready)
            return
        profile = self._device_profiles.get(dev)
        facts = device_facts(dev, cached_only=True)
        token = self._device_mount_tokens.get(dev, 0)
        if from_drop and drop_target != (token,facts.volume_uuid):
            self._flash_status('USB changed; choose the connected target again' if standalone else
                               'USB changed after the drop; drop the playlist onto the connected device again',15)
            return
        if not profile or profile.kind != 'generic_usb' or not getattr(profile,'cdj_export',profile.rockbox_export) or not facts.writable or not facts.volume_uuid:
            QMessageBox.warning(self, 'CDJ export unavailable', 'Select a writable FAT32 or exFAT USB.')
            return
        self._release_eject_ready()
        self._exporting = True
        self._cdj_plan = None
        recovery = self._data_file.parent/'cdj-recovery'
        # Freeze curation dictionaries and playlist selection on the GUI thread.
        library, titles, artists = deepcopy(self.lib), dict(self.titles), dict(self.overrides)
        playlists = deepcopy(tuple(playlists))
        standalone = deepcopy(tuple(standalone))
        all_playlists = deepcopy(self._sync_playlists())
        global_profile = deepcopy(self.settings.get('cdj_my_settings',{}))
        if settings is None:
            operation = lambda cancel, progress: cdj_export.plan_export(
                library, playlists, dev, token=token, volume_uuid=facts.volume_uuid,
                all_playlists=all_playlists, titles=titles, artists=artists, art_cache=self.art_cache,
                should_cancel=cancel, on_progress=progress, recovery_root=recovery, additive=from_drop,
                global_profile=global_profile,standalone=standalone)
        else:
            changes = dict(settings)
            operation = lambda cancel, _progress: cdj_export.plan_settings(
                dev, changes, token=token, volume_uuid=facts.volume_uuid,
                sources=[Path(library.source), *[t.path for t in library.tracks_by_id.values()],
                         *[Path(t.source_analysis_path) for t in library.tracks_by_id.values() if t.source_analysis_path]],
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
        if isinstance(result, Exception):
            QMessageBox.warning(self, 'CDJ USB operation stopped', str(result))
            self._flash_status('CDJ USB operation stopped', 15)
        else:
            self._flash_status('CDJ export cancelled — nothing written' if cancelled else
                               'CDJ USB already up to date' if unchanged else 'CDJ USB updated ✓', 15)
        self._usb_view_requests.add(dev)
        self._refresh_devices(force=True)
        self._update_device_action_state()

    def _apply_usb_settings(self, changes):
        dev = self._current_device_path
        if dev:
            self._begin_cdj_export(dev, settings=changes)

    def _sync_usb_my_settings(self, snapshot):
        from soundbox import my_settings
        native = snapshot.native if snapshot else None
        current = my_settings.values(native.files) if native else {}
        if current and not self.settings.get('cdj_my_settings'):
            self.settings['cdj_my_settings'] = current
            if not self._save_state(invalidate_views=False):
                self.settings.pop('cdj_my_settings',None)
                return
        profile = self.settings.get('cdj_my_settings',{})
        self.usb_device_pages.global_profile = deepcopy(profile)
        editor=self.usb_device_pages.my_settings
        if editor.profile!=current or editor.global_profile!=profile:
            editor.set_profile(current,profile,preserve_edits=True)
        if self._global_my_settings.profile!=profile:self._global_my_settings.set_profile(profile)

    def _save_global_my_settings(self, profile):
        from soundbox import my_settings
        profile = my_settings.validate(profile)
        previous = self.settings.get('cdj_my_settings',{})
        self.settings['cdj_my_settings'] = deepcopy(profile)
        if not self._save_state(invalidate_views=False):
            self.settings['cdj_my_settings'] = previous
            return
        self._global_my_settings.set_profile(profile)
        self.usb_device_pages.global_profile = deepcopy(profile)
        native = getattr(self.usb_device_pages._snapshot,'native',None)
        self.usb_device_pages.my_settings.set_profile(my_settings.values(native.files) if native else {},profile)
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
        if self._defer_device_operation_until_scan_finishes(lambda:self._reformat_usb(dev)):return
        if self._device_operation_owned():return
        from soundbox import usb_format
        profile=self._device_profiles.get(dev)
        if profile is None or profile.kind!='generic_usb':return
        facts=device_facts(dev,cached_only=True);token=self._device_mount_tokens.get(dev,0)
        sources=[Path(self.lib.source),*[t.path for t in self.lib.tracks_by_id.values()]] if self.lib else []
        self._exporting=True;self._release_eject_ready()
        worker=CdjWorker(lambda _cancel,_progress:usb_format.capture(dev,token=token,volume_uuid=facts.volume_uuid,sources=sources))
        self._cdj_worker=worker
        def show(result):
            self._stop_pulse()
            if isinstance(result,Exception):self._finish_cdj(dev,result);return
            from .usb_preferences import FormatUsbDialog
            dialog=FormatUsbDialog(result,self)
            if dialog.exec()!=dialog.Accepted or self._shutting_down:
                self._exporting=False;self._update_device_action_state();return
            global_profile=deepcopy(self.settings.get('cdj_my_settings',{}))
            format_name,format_filesystem=dialog.choice
            recovery=self._data_file.parent/'cdj-recovery'
            def format_and_initialize(_cancel,_progress):
                info=usb_format.format_usb(result,format_name,format_filesystem,sources=sources)
                path=Path(info['MountPoint'])
                plan=cdj_export.plan_initialize(path,token=path.stat().st_dev,volume_uuid=info['VolumeUUID'],
                    sources=sources,profile=global_profile,name=info['VolumeName'],recovery_root=recovery)
                try:cdj_transaction.commit(plan)
                finally:plan.close()
                return str(path)
            next_worker=CdjWorker(format_and_initialize);self._cdj_worker=next_worker
            def finished(value):
                self._finish_cdj(dev,value if isinstance(value,Exception) else None)
                if not isinstance(value,Exception):self._flash_status('USB formatted · Global My Settings loaded',15)
            next_worker.finished.connect(lambda w=next_worker:self._finish_device_result_worker('_cdj_worker',w,finished))
            self.cancel_btn.setVisible(False);self._start_pulse('Formatting USB')
            self._start_device_worker(next_worker,dev,'format')
            self.cancel_btn.setVisible(False)
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,show))
        self._start_pulse('Reading USB');self._start_device_worker(worker,dev,'inspect')

    def _recover_cdj(self, dev):
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
        if (self.lib and song.source_db_id == self.lib.source_library_id and song.source_id in self.lib.tracks_by_id
                and song.artwork):
            row['art'] = self.art_cache.status_kind(song.source_id)

    def _load_usb_song(self, song):
        snapshot = self.usb_device_pages._snapshot
        if not hasattr(snapshot,'target') or snapshot.error:
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

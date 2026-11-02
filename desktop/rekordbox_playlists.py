"""Native playlist gestures and a durable FIFO on the shared source worker lane."""
from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from soundbox import engine
from soundbox.library import Playlist
from soundbox.rekordbox_playlist_store import PlaylistStore, load_authority, request_for, validate_request
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed
from .choices import Menu
from .trees import COLLECTION_ROLE, PATH_ROLE, RB_NODE_ROLE

QUEUE='rekordbox_playlist_edits'
LOG=logging.getLogger('soundbox')




class PlaylistEditController(QObject):
    def __init__(self, window, *, store_factory=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=store_factory;self.closing=False;self.not_before=0.0
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule);self.tags.changed.connect(self.schedule)

    @property
    def pending(self):
        value=self.window.settings.get(QUEUE,[])
        return value if isinstance(value,list) else []

    def queue_error(self):
        jobs=self.window.settings.get(QUEUE,[])
        try:
            if not isinstance(jobs,list) or len(jobs)>256:raise ValueError('Unknown queue')
            for job in jobs:
                if (not isinstance(job,dict) or type(job.get('source')) is not str
                        or type(job.get('library_id')) is not str or not job['library_id']
                        or type(job.get('error')) is not str):raise ValueError('Unknown intent')
                validate_request(job['request'])
        except (KeyError,TypeError,ValueError,WriteProofError):
            return 'Saved native playlist edit queue is invalid; evidence preserved'
        return ''

    def reason(self):
        if self.queue_error():return self.queue_error()
        w=self.window;lib=w.lib
        if self.closing or w._shutting_down or w._state_maintenance:return 'Library operation in progress'
        if not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified or not lib.source_playlist_catalog:
            return 'Refresh the native rekordbox library to manage playlists'
        if not self.factory and load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',
                                              lib.source,lib.source_library_id) is None:
            return 'Native playlist editing is not enabled for this library'
        return ''

    def status(self,message):
        if self.tags.playlist_update_status!=message:
            previous=self.tags.playlist_update_status
            self.tags.playlist_update_status=message;self.tags.changed.emit()
            w=self.window
            if not message and previous and getattr(w,'_prep_update_message',None)==previous:
                w._prep_update_message='';w._set_status_text('Ready')

    def enqueue(self, request):
        started=time.monotonic()
        validate_request(request)
        reason=self.reason()
        if reason:self.window._flash_status(reason,10);return False
        if len(self.pending)>=256:
            self.window._flash_status('Wait for pending playlist edits to finish',10);return False
        w=self.window;old=deepcopy(w.settings.get(QUEUE,[]))
        job=dict(source=str(w.lib.source),library_id=w.lib.source_library_id,request=deepcopy(request),error='',retry=False)
        w.settings[QUEUE]=[*old,job]
        if not w._save_state():
            if old:w.settings[QUEUE]=old
            else:w.settings.pop(QUEUE,None)
            return False
        admitted=time.monotonic()
        w._native_pending_changed(rebuild=True)
        LOG.info('Playlist intent admitted: operation=%s persist_ms=%.2f feedback_ms=%.2f',
                 request['operation'],(admitted-started)*1000,(time.monotonic()-started)*1000)
        self.status('Updating playlist…');self.schedule();return True

    def schedule(self):
        if self.closing:return
        if self.queue_error():self.status(self.queue_error());return
        if self.pending:
            if self.queue_error():self.status(self.queue_error())
            elif self.pending[0].get('error'):self.status('Rekordbox playlist edit needs attention — '+self.pending[0]['error'])
            elif not self.tags.playlist_update_status:self.status('Playlist edits pending')
            QTimer.singleShot(0,self.pump)

    def pump(self):
        w=self.window
        if self.closing or not self.pending:return
        if self.queue_error():self.status(self.queue_error());return
        if self.pending[0].get('error'):return
        imports=getattr(w,'shared_import_controller',None)
        if imports and imports.source_priority:self.timer.start();return
        if (self.tags.owned or w._refresh_worker is not None or w._state_maintenance
                or w._device_operation_owned()):
            self.timer.start();return
        if self.reason():return
        if time.monotonic()<self.not_before:
            self.timer.start();return
        job=deepcopy(self.pending[0]);lib=w.lib
        if job['source']!=str(lib.source) or job['library_id']!=lib.source_library_id:
            self.fail('Pending playlist edit belongs to a different rekordbox library');return
        def work():
            # This subprocess check belongs in the worker, never a transport or
            # GUI callback. A running/unknown native owner postpones publication.
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            store=(self.factory(lib) if self.factory else
                   PlaylistStore(load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',
                                               lib.source,lib.source_library_id),self.tags._write_root(lib)))
            if job.get('retry'):return dict(retry_request=store.prepare_retry(job['request']))
            return dict(catalog=store.apply(job['request']))
        def completed(worker):
            if worker.error:self.fail(worker.error);return
            if worker.result.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Playlist edit pending — quit rekordbox to save')
                if not self.closing:self.timer.start()
                return
            if 'retry_request' in worker.result:
                previous=deepcopy(w.settings.get(QUEUE,[]));jobs=deepcopy(previous)
                jobs[0].update(request=worker.result['retry_request'],error='',retry=False)
                w.settings[QUEUE]=jobs
                if not w._save_state():
                    w.settings[QUEUE]=previous;self.fail('Could not save the renewed playlist intent');return
                self.not_before=0;self.schedule();return
            before=deepcopy(w.settings.get(QUEUE,[]));tail=deepcopy(before[1:])
            new_catalog=worker.result['catalog']
            if not w.lib or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']:
                self.fail('Saved in the original rekordbox library; switch back to finish this edit');return
            # Rebase only this FIFO's own still-unstarted edits. External source
            # changes remain conflicts; a worker never silently retargets them.
            for later in tail:
                if (later['source']==job['source'] and later['library_id']==job['library_id']
                        and later['request']['revision']==job['request']['revision']):
                    later['request']['revision']=new_catalog['revision']
            w.settings[QUEUE]=tail
            w._native_apply_catalog(new_catalog,job['request'])
            if not w._save_state():
                w.settings[QUEUE]=before
                self.fail('Saved in rekordbox; SoundBox state could not be saved. Retry after fixing storage.');return
            self.not_before=0
            if not self.closing:w._native_refresh_view(job['request'])
            self.status('Updating playlist…' if self.pending else '')
            finish=getattr(w,'_finish_edit_status',None)
            if not self.pending and finish:finish(quiet=True)
            self.schedule()
        self.tags.run_playlist_job(work,completed)

    def fail(self, message):
        self.timer.stop();w=self.window
        jobs=deepcopy(self.pending)
        if jobs:
            jobs[0]['error']=message;w.settings[QUEUE]=jobs;w._save_state()
        w._native_pending_changed(rebuild=True)
        w._native_reconcile_source()
        self.status('Rekordbox playlist edit needs attention — '+message)

    def retry(self):
        if not self.pending:return
        if self.queue_error():self.window._flash_status(self.queue_error(),15);return
        w=self.window;previous=deepcopy(self.pending);jobs=deepcopy(previous)
        jobs[0].update(error='',retry=True);w.settings[QUEUE]=jobs
        if w._save_state():
            w._native_pending_changed(rebuild=True)
            self.status('Updating playlist…');self.not_before=0;self.schedule()
        else:w.settings[QUEUE]=previous

    def discard(self):
        if not self.pending or self.tags.owned:return
        w=self.window
        if QMessageBox.question(w,'Discard pending playlist edits',
                f'Discard {len(self.pending)} pending playlist edit(s)?\nEdits already saved in rekordbox remain saved.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        previous=deepcopy(self.pending);w.settings[QUEUE]=[]
        if not w._save_state():w.settings[QUEUE]=previous
        else:
            self.timer.stop();self.status('');w._native_pending_changed(rebuild=True)
            w._native_reconcile_source()

    def stop(self):
        self.closing=True;self.timer.stop()


class NativePlaylistsMixin:
    def _native_reconcile_source(self):
        """Request a fresh owned snapshot after a failed/discarded source intent."""
        refresh=getattr(self,'_start_rekordbox_refresh',None)
        if (not refresh or self._shutting_down or not self.lib
                or self.lib.source_kind!='rekordbox-db'):return
        if self.tag_controller.owned or getattr(self,'_refresh_worker',None) is not None:
            self._refresh_pending=True
        else:
            refresh(Path(self.lib.source))

    def _native_presentation(self):
        controller=getattr(self,'playlist_controller',None)
        if not self.lib or not controller or controller.queue_error():return self.lib
        from soundbox.playlist_presentation import project
        library=project(self.lib,controller.pending)
        imports=getattr(self,'shared_import_controller',None)
        if imports and not imports.queue_error():
            from soundbox.shared_imports import present
            library=present(library,imports.pending)
        return library

    def _native_pending_deletions(self):
        """Rows absent from this source's durable presentation, including new IDs."""
        controller=getattr(self,'playlist_controller',None);lib=self.lib
        if not lib or not controller or controller.queue_error() or not controller.pending:return set()
        known={r['ID'] for r in lib.source_playlist_catalog.get('nodes',[]) if r['rb_local_deleted']==0}
        known|={job['request']['playlist_id'] for job in controller.pending
                if job['source']==str(lib.source) and job['library_id']==lib.source_library_id
                and job['request']['operation']=='create'}
        visible={r['ID'] for r in self._native_presentation().source_playlist_catalog.get('nodes',[]) if r['rb_local_deleted']==0}
        return known-visible

    def _native_pending_changed(self, *, rebuild=False):
        # The small test controller host has no sidebar; source behavior remains
        # independently testable. The desktop applies this in the admission turn.
        if not hasattr(self,'tree') or self.lib is None:return
        removed=self._native_pending_deletions()
        identity=(str(self.lib.source),self.lib.source_library_id) if self.lib else None
        expanded=(set(getattr(self,'_pending_native_expansion',set()))
                  if identity==getattr(self,'_pending_native_identity',None) else set())
        self._pending_native_identity=identity
        def remember(parent):
            for i in range(parent.childCount()):
                item=parent.child(i)
                if item.isExpanded():expanded.add(item.data(0,RB_NODE_ROLE))
                remember(item)
        remember(self.tree.invisibleRootItem())
        self._pending_native_expansion=expanded
        current=getattr(self,'_view_key','') or ''
        active=getattr(self,'_active_native_playlist_id',None)
        if current.startswith('rb:') and not active:
            active=next((p.pid for p in self.lib.playlists if p.display_path==current[3:]),None)
        if current.startswith('rb:') and active in removed:
            self._stash_view();self._active_native_playlist_id=None
            self._select_only(self.sb_tree)
            item=self.sb_tree.topLevelItem(0)
            if item and item.data(0,COLLECTION_ROLE):
                self.sb_tree.blockSignals(True)
                try:self.sb_tree.setCurrentItem(item)
                finally:self.sb_tree.blockSignals(False)
            self._show_collection()
            self._restore_view('sb:Collection')
        if rebuild:
            if current.startswith('rb:') and active:
                updated=next((p for p in self._native_presentation().playlists if p.pid==active),None)
                if updated:self._view_key='rb:'+updated.display_path
            self._populate_tree(engine.summary(self.lib))
            if current.startswith('rb:') and active not in removed:self._reload_current_view()
            def restore(parent):
                for i in range(parent.childCount()):
                    item=parent.child(i)
                    if item.data(0,RB_NODE_ROLE) in expanded:item.setExpanded(True)
                    restore(item)
            self._accordion_guard=True
            try:restore(self.tree.invisibleRootItem())
            finally:self._accordion_guard=False
        else:
            def prune(parent):
                for i in range(parent.childCount()-1,-1,-1):
                    item=parent.child(i)
                    if item.data(0,RB_NODE_ROLE) in removed:parent.takeChild(i)
                    else:prune(item)
            self.tree.blockSignals(True)
            try:prune(self.tree.invisibleRootItem())
            finally:self.tree.blockSignals(False)

    def _native_view_playlist(self,path):
        lib=self._native_presentation()
        if not lib:return None
        identity=getattr(self,'_active_native_playlist_id',None)
        return next((p for p in lib.playlists if p.pid==identity and p.display_path==path),None) or next(
            (p for p in lib.playlists if p.display_path==path),None)

    def _native_node(self, identity):
        lib=self._native_presentation()
        return next((r for r in lib.source_playlist_catalog.get('nodes',[]) if r['ID']==identity),None) if lib else None

    def _native_request(self, operation, **kwargs):
        reason=self.playlist_controller.reason()
        if reason:self._flash_status(reason,10);return False
        # Only target identity/presentation comes from the projected view. The
        # captured revision remains the verified source baseline; FIFO rebase
        # renews it after preceding accepted operations commit.
        try:request=request_for(self._native_presentation().source_playlist_catalog,operation,**kwargs)
        except (ValueError,KeyError) as exc:self._flash_status(str(exc),10);return False
        accepted=self.playlist_controller.enqueue(request)
        if accepted and operation=='create' and hasattr(self,'tree'):
            item=self._find_tree_item('',request['playlist_id'])
            if item:
                if item.data(0,PATH_ROLE):self.tree.setCurrentItem(item)
                else:item.setExpanded(True)
        return accepted

    def _native_new(self, parent='root', *, folder=False):
        label='Folder' if folder else 'Playlist'
        name,ok=QInputDialog.getText(self,'New rekordbox '+label,'Name:')
        name=name.strip()
        if not ok or not name:return
        if any(r['ParentID']==parent and r['Name']==name and r['rb_local_deleted']==0
               for r in self._native_presentation().source_playlist_catalog['nodes']):
            self._flash_status('A playlist or folder with that name already exists here',8);return
        self._native_request('create',name=name,parent=parent,folder=folder)

    def _native_rename(self, identity):
        row=self._native_node(identity)
        if row is None:return
        item=self._find_tree_item('',identity=identity)
        self.tree.rename_inline(item,row['Name'],lambda name:self._native_rename_commit(identity,row['Name'],name))

    def _native_rename_commit(self, identity, previous, name):
        row=self._native_node(identity)
        if row is None or row['Name']!=previous:
            self._flash_status('Playlist changed before renaming; try again',8);return
        name=name.strip()
        if any(other['ID']!=identity and other['ParentID']==row['ParentID'] and other['Name']==name
               and other['rb_local_deleted']==0 for other in self._native_presentation().source_playlist_catalog['nodes']):
            self._flash_status('A playlist or folder with that name already exists here',8);return
        if name and name!=row['Name']:
            self._native_request('rename',identity=identity,name=name)

    def _native_delete(self, identity):
        row=self._native_node(identity)
        if row is None:return
        kind='folder and its playlists' if row['Attribute']==1 else 'playlist'
        if QMessageBox.question(self,'Delete from rekordbox',
                f'Delete the rekordbox {kind} “{row["Name"]}”?\nSongs remain in Collection and audio files are preserved.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            self._native_request('delete',identity=identity)

    def _native_append(self, identity, ids):
        tracks=[self.lib.tracks_by_id.get(tid) for tid in ids] if self.lib else []
        if any(t is None or not t.source_uuid or t.imported for t in tracks):
            controller=getattr(self,'shared_import_controller',None)
            row=self._native_node(identity)
            if controller and row and row['Attribute']==0 and tracks and all(t is not None for t in tracks):
                self._queue_shared_import(tracks,dict(kind='native',id=identity,uuid=row['UUID']))
            else:self._flash_status('Refresh the library before adding these tracks',10)
            return
        row=self._native_node(identity)
        if row is None or row['Attribute']!=0:return
        existing={r['ContentID'] for r in self.lib.source_playlist_catalog['members']
                  if r['PlaylistID']==identity and r['rb_local_deleted']==0}
        duplicates=[t.track_id for t in tracks if t.native_id in existing]
        allow=bool(duplicates) and self._confirm_duplicate_add(row['Name'],duplicates)
        selected=tracks if allow else [t for t in tracks if t.native_id not in existing]
        if selected:self._native_request('append',identity=identity,tracks=selected,allow_duplicate=allow)

    def _native_remove(self, path, ids):
        pl=self._native_view_playlist(path)
        if pl is None:return
        tracks=[self.lib.tracks_by_id.get(tid) for tid in ids]
        if any(t is None or not t.source_uuid for t in tracks):
            self._flash_status('Refresh the native track identities before removing them',10);return
        self._native_request('remove',identity=pl.pid,tracks=tracks)

    def _native_menu(self, item):
        menu=Menu(self)
        self._native_menu_actions(menu,item)
        return menu

    def _native_menu_actions(self,menu,item=None):
        identity=item.data(0,RB_NODE_ROLE) if item else None
        row=self._native_node(identity);parent=(row['ID'] if row and row['Attribute']==1 else row['ParentID'] if row else 'root')
        reason=self.playlist_controller.reason()
        for label,callback,available in (
            ('New Playlist',lambda:self._native_new(parent),True),
            ('New Folder',lambda:self._native_new(parent,folder=True),True),
            ('Rename',lambda:self._native_rename(identity),bool(row and row['Attribute'] in (0,1))),
            ('Delete',lambda:self._native_delete(identity),bool(row and row['Attribute'] in (0,1)))):
            action=menu.addAction(label,callback);action.setEnabled(not reason and available)
            if reason:action.setToolTip(reason)
        if self.playlist_controller.pending:
            menu.addSeparator();menu.addAction('Retry pending edits',self.playlist_controller.retry)
            discard=menu.addAction('Discard pending edits',self.playlist_controller.discard)
            discard.setEnabled(not self.tag_controller.owned)
        prep=getattr(self,'prep_controller',None)
        if prep and (prep.pending or prep.errors):
            menu.addAction('Retry cue/grid saves',prep.retry)
        publisher=getattr(self,'native_analysis_controller',None)
        if publisher and publisher.pending:
            menu.addSeparator();menu.addAction('Retry sharing analysis',publisher.retry)
        imports=getattr(self,'shared_import_controller',None)
        if imports and imports.pending:
            menu.addSeparator();menu.addAction('Retry pending imports',imports.retry)
            discard=menu.addAction('Discard pending imports',imports.discard)
            discard.setEnabled(not self.tag_controller.owned)

    def _native_tree_menu(self,pos):
        self._native_menu(self.tree.itemAt(pos)).exec(self.tree.viewport().mapToGlobal(pos))

    def _native_apply_catalog(self,catalog,request):
        lib=self.lib;old={p.pid:p for p in lib.playlists}
        native_tracks={t.native_id:t for t in lib.tracks_by_id.values()}
        active={r['ID']:r for r in catalog['nodes'] if r['rb_local_deleted']==0 and r['Attribute'] in (0,1,4)}
        children={}
        for row in active.values():children.setdefault(row['ParentID'],[]).append(row)
        by_playlist={}
        for member in catalog['members']:
            if member['rb_local_deleted']==0:by_playlist.setdefault(member['PlaylistID'],[]).append(member)
        result=[]
        def walk(parent,path,seen):
            for row in sorted(children.get(parent,[]),key=lambda r:r['Seq']):
                if row['ID'] in seen:raise ValueError('Native folder cycle')
                if row['Attribute']==1:walk(row['ID'],path+[row['Name']],seen|{row['ID']})
                elif row['Attribute']==0:
                    keys=[native_tracks[r['ContentID']].track_id if r['ContentID'] in native_tracks else r['ContentID']
                          for r in sorted(by_playlist.get(row['ID'],[]),key=lambda r:r['TrackNo'])]
                    result.append(Playlist(row['Name'],path,keys,row['ID']))
                elif row['ID'] in old:
                    result.append(Playlist(row['Name'],path,list(old[row['ID']].keys),row['ID']))
        walk('root',[],set())
        # Retain affected native tracks in Collection after their final playlist
        # reference disappears. Exact UUID pins survive refresh/restart, confer
        # no source permission and never register/retag audio.
        pins=self.settings.setdefault('rekordbox_collection_tracks',{}).setdefault(lib.source_library_id,{})
        affected={t['track_id'] for t in request['tracks']}
        if request['operation']=='delete':
            affected|={lib.tracks_by_id[tid].native_id for p in lib.playlists if p.pid not in active for tid in p.keys if tid in lib.tracks_by_id}
        for tid in affected:
            track=native_tracks.get(tid)
            if track and track.source_uuid:pins[tid]=track.source_uuid
        current=getattr(self,'_view_key','') or ''
        prior=(self._native_view_playlist(current[3:]).pid if current.startswith('rb:') and self._native_view_playlist(current[3:]) else None)
        lib.playlists=result;lib.source_playlist_catalog=catalog
        if request['operation']=='remove':
            bag=self.removals.get(request['playlist_id'],{})
            for track in request['tracks']:
                current_track=native_tracks.get(track['track_id'])
                bag.pop(current_track.track_id if current_track else track['track_id'],None)
            if not bag:self.removals.pop(request['playlist_id'],None)
        if prior:
            updated=next((p for p in result if p.pid==prior),None)
            self._view_key='rb:'+updated.display_path if updated else 'sb:Collection'
        self._apply_settings_to_lib()

    def _native_refresh_view(self,request):
        self._populate_tree(engine.summary(self.lib));self._reload_current_view()
        self._cache_library();self._refresh_devices();self._refresh_graveyard()

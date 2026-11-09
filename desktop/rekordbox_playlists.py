"""Native playlist gestures and a durable FIFO on the shared source worker lane."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import logging
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from soundbox import engine
from soundbox.library import Playlist
from soundbox.rekordbox_playlist_store import (PlaylistStore, load_authority, request_for, validate_request,
    command_requests, validate_collection_pins)
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed
from .choices import Menu, exec_transient_menu
from .trees import (ARTISTS_ROLE, COLLECTION_ROLE, CUSTOM_ROLE, NATIVE_TREE_ROLE,
                    PATH_ROLE, RB_NODE_ROLE, PINNED_TREE_ROLE, sidebar_choice_icon)

QUEUE='rekordbox_playlist_edits'
LOG=logging.getLogger('soundbox')


def _remember_collection_pins(settings,library_id,pins):
    validate_collection_pins(pins)
    groups=settings.get('rekordbox_collection_tracks',{})
    if not isinstance(groups,dict) or not isinstance(groups.get(library_id,{}),dict):
        raise WriteProofError('Saved native Collection identities are invalid')
    current=groups.get(library_id,{})
    if any(identity in current and current[identity]!=uuid for identity,uuid in pins.items()):
        raise WriteProofError('Captured native Collection identity conflicts with a saved retention identity')
    settings['rekordbox_collection_tracks']={**groups,library_id:{**current,**pins}}




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
                if 'collection_pins' in job:
                    if job['request']['operation']!='delete':raise ValueError('Unknown Collection retention intent')
                    validate_collection_pins(job['collection_pins'])
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

    def reorder_reason(self):
        reason=self.reason()
        if reason or self.factory:return reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return '' if authority and authority.reorder_accepted else 'Native playlist reordering awaits acceptance for this library'

    def enqueue(self, request):
        started=time.monotonic()
        validate_request(request)
        reason=self.reorder_reason() if request['operation']=='reorder' else self.reason()
        if reason:self.window._flash_status(reason,10);return False
        if len(self.pending)>=256:
            self.window._flash_status('Wait for pending playlist edits to finish',10);return False
        # A bounded create+append batch can capture a target's independent XML
        # clock a few milliseconds ahead. A rapid subsequent gesture must
        # advance that exact queued clock before its own durable admission.
        # Never infer it from SQL time or read source XML on the GUI thread.
        request=deepcopy(request);clocks={}
        for pending in self.pending:
            if (pending['source']!=str(self.window.lib.source)
                    or pending['library_id']!=self.window.lib.source_library_id):continue
            for command in command_requests(pending['request']):
                identity=command['playlist_id'];clocks[identity]=max(clocks.get(identity,0),command['xml_timestamp'])
        for command in command_requests(request):
            identity=command['playlist_id']
            command['xml_timestamp']=max(command['xml_timestamp'],clocks.get(identity,0)+1)
            clocks[identity]=command['xml_timestamp']
        try:validate_request(request)
        except WriteProofError as exc:
            self.window._flash_status(str(exc),10);return False
        w=self.window;missing=object();old=w.settings.get(QUEUE,missing)
        job=dict(source=str(w.lib.source),library_id=w.lib.source_library_id,request=request,error='',retry=False)
        w.settings[QUEUE]=[*(old if old is not missing else []),job]
        if not w._save_state():
            if old is missing:w.settings.pop(QUEUE,None)
            else:w.settings[QUEUE]=old
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
            if job['request']['operation']=='delete':
                if not hasattr(store,'prepare_collection_pins'):
                    raise WriteProofError('Native playlist deletion needs exact Collection retention capture')
                if 'collection_pins' not in job:
                    return dict(collection_pins=store.prepare_collection_pins(job['request']))
                return dict(catalog=store.apply(job['request'],collection_pins=job['collection_pins']))
            return dict(catalog=store.apply(job['request']))
        def completed(worker):
            if worker.error:self.fail(worker.error);return
            if worker.result.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Playlist edit pending — quit rekordbox to save')
                if not self.closing:self.timer.start()
                return
            if (not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id'])
                    or not self.pending or self.pending[0]['request']!=job['request']):
                self.fail('Saved in the original rekordbox library; switch back to finish this edit');return
            if 'collection_pins' in worker.result:
                previous=dict(w.settings)
                try:
                    pins=worker.result['collection_pins']
                    _remember_collection_pins(w.settings,job['library_id'],pins)
                    queue=list(self.pending);queue[0]={**queue[0],'collection_pins':pins};w.settings[QUEUE]=queue
                    if not w._save_state():raise OSError('Could not persist native Collection retention; playlist deletion was not started')
                except (OSError,ValueError,WriteProofError) as exc:
                    w.settings.clear();w.settings.update(previous);self.fail(str(exc));return
                self.not_before=0;self.schedule();return
            if 'retry_request' in worker.result:
                previous=w.settings.get(QUEUE,[]);jobs=list(previous)
                jobs[0]={**jobs[0],'request':worker.result['retry_request'],'error':'','retry':False}
                if worker.result['retry_request']!=job['request']:jobs[0].pop('collection_pins',None)
                w.settings[QUEUE]=jobs
                if not w._save_state():
                    w.settings[QUEUE]=previous;self.fail('Could not save the renewed playlist intent');return
                self.not_before=0;self.schedule();return
            before=w.settings.get(QUEUE,[]);tail=list(before[1:])
            new_catalog=worker.result['catalog']
            if not w.lib or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']:
                self.fail('Saved in the original rekordbox library; switch back to finish this edit');return
            # Rebase only this FIFO's own still-unstarted edits. External source
            # changes remain conflicts; a worker never silently retargets them.
            for index,later in enumerate(tail):
                if (later['source']==job['source'] and later['library_id']==job['library_id']
                        and later['request']['revision']==job['request']['revision']):
                    later=deepcopy(later);tail[index]=later
                    later['request']['revision']=new_catalog['revision']
                    for command in command_requests(later['request']):command['revision']=new_catalog['revision']
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
        jobs=list(self.pending)
        if jobs:
            jobs[0]={**jobs[0],'error':message};w.settings[QUEUE]=jobs;w._save_state()
        w._native_pending_changed(rebuild=True)
        w._native_reconcile_source()
        self.status('Rekordbox playlist edit needs attention — '+message)

    def retry(self):
        if not self.pending:return
        if self.queue_error():self.window._flash_status(self.queue_error(),15);return
        w=self.window;previous=self.pending;jobs=list(previous)
        jobs[0]={**jobs[0],'error':'','retry':True};w.settings[QUEUE]=jobs
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
        previous=self.pending;w.settings[QUEUE]=[]
        if not w._save_state():w.settings[QUEUE]=previous
        else:
            self.timer.stop();self.status('');w._native_pending_changed(rebuild=True)
            w._native_reconcile_source()

    def stop(self):
        self.closing=True;self.timer.stop()


class NativePlaylistsMixin:
    def _local_playlist_icon_key(self,name):
        lib=self.lib
        return json.dumps(['local',str(lib.source) if lib else '',
                           lib.source_library_id if lib else '',name],separators=(',',':'))

    def _playlist_icon_key(self,index):
        if (not index.isValid() or index.data(COLLECTION_ROLE) or index.data(ARTISTS_ROLE)
                or not (index.data(PATH_ROLE) or index.data(CUSTOM_ROLE))):return None
        if not index.data(NATIVE_TREE_ROLE):
            return self._local_playlist_icon_key(index.data(CUSTOM_ROLE)) if index.data(CUSTOM_ROLE) else None
        lib=self.lib
        if not lib or lib.source_kind!='rekordbox-db':return None
        nodes=(lib.source_playlist_catalog or {}).get('nodes',[])
        scope=(str(lib.source),lib.source_library_id)
        cached=getattr(self,'_playlist_icon_catalog',None)
        if cached is None or cached[0]!=scope or cached[1] is not nodes:
            cached=(scope,nodes,{row['ID']:row for row in nodes})
            self._playlist_icon_catalog=cached
        row=cached[2].get(index.data(RB_NODE_ROLE))
        if (not row or row.get('rb_local_deleted')!=0 or row.get('Attribute') not in (0,4)
                or not row.get('UUID')):return None
        return json.dumps(['native',*scope,row['ID'],row['UUID']],separators=(',',':'))

    def _playlist_icon_kind(self,index,default):
        values=self.settings.get('playlist_icons',{})
        kind=values.get(self._playlist_icon_key(index)) if isinstance(values,dict) else None
        return kind if kind in ('star','note','crate') else default

    def _set_playlist_icon(self,tree,index,expected,kind):
        if (expected is None or kind not in ('star','note','crate')
                or expected!=self._playlist_icon_key(index)):
            self._flash_status('That playlist changed; open its icon menu again',8);return False
        if getattr(self,'_state_maintenance',False) or getattr(self,'_shutting_down',False):return False
        previous=self.settings.get('playlist_icons');present='playlist_icons' in self.settings
        values=dict(previous) if isinstance(previous,dict) else {}
        values[expected]=kind;self.settings['playlist_icons']=values
        if not self._save_state():
            if present:self.settings['playlist_icons']=previous
            else:self.settings.pop('playlist_icons',None)
            return False
        tree.viewport().update()
        return True

    def _playlist_icon_menu(self,tree,item):
        from PySide6.QtCore import QPersistentModelIndex
        index=QPersistentModelIndex(tree.indexFromItem(item))
        key=self._playlist_icon_key(index)
        if key is None:return None
        current=tree.sidebar_icon_kind(index);menu=Menu(self)
        for label,kind in (('Star','star'),('Music note','note'),('Vinyl crate','crate')):
            action=menu.addAction(sidebar_choice_icon(kind),label)
            action.setCheckable(True);action.setChecked(kind==current)
            action.triggered.connect(lambda _checked=False,k=kind:self._set_playlist_icon(tree,index,key,k))
        return menu

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
        if not self.lib or not controller or controller.queue_error():
            self._native_presentation_cache=None
            return self.lib
        from soundbox.playlist_presentation import project,copy_catalog
        imports=getattr(self,'shared_import_controller',None)
        pending=controller.pending
        if pending and self.lib.source_playlist_catalog and not (imports and imports.pending):
            lib=self.lib;catalog=lib.source_playlist_catalog
            # Compare native scalar rows and copied durable intents by value,
            # including in-place UUID/error/clock changes. Object identity alone
            # cannot establish a valid presentation after source reconciliation.
            identities=tuple((key,t.track_id,t.native_id,t.source_uuid) for key,t in lib.tracks_by_id.items())
            smart={r['ID'] for r in catalog.get('nodes',[]) if r.get('Attribute')==4}
            intelligent=tuple((p.pid,tuple(p.keys)) for p in lib.playlists if p.pid in smart)
            inputs=(str(lib.source),lib.source_library_id,identities,intelligent)
            cached=getattr(self,'_native_presentation_cache',None)
            if not (cached and cached[0]==inputs and cached[1]==catalog and cached[2]==pending):
                projected=project(lib,pending)
                cached=(inputs,copy_catalog(catalog),deepcopy(pending),projected.playlists,projected.source_playlist_catalog)
                self._native_presentation_cache=cached
            # Current filters, hidden IDs, artist registry, tracks, source tags
            # and other library metadata always come from the current Library.
            library=replace(lib,playlists=cached[3],source_playlist_catalog=cached[4])
        else:
            self._native_presentation_cache=None
            library=project(self.lib,pending)
        if imports and not imports.queue_error():
            from soundbox.shared_imports import present
            library=present(library,imports.pending)
        return library

    def _native_pending_deletions(self, *, presentation=None):
        """Rows absent from this source's durable presentation, including new IDs."""
        controller=getattr(self,'playlist_controller',None);lib=self.lib
        if not lib or not controller or controller.queue_error() or not controller.pending:return set()
        known={r['ID'] for r in lib.source_playlist_catalog.get('nodes',[]) if r['rb_local_deleted']==0}
        known|={command['playlist_id'] for job in controller.pending
                if job['source']==str(lib.source) and job['library_id']==lib.source_library_id
                for command in command_requests(job['request']) if command['operation']=='create'}
        presentation=self._native_presentation() if presentation is None else presentation
        visible={r['ID'] for r in presentation.source_playlist_catalog.get('nodes',[]) if r['rb_local_deleted']==0}
        return known-visible

    def _native_pending_changed(self, *, rebuild=False,presentation=None):
        # The small test controller host has no sidebar; source behavior remains
        # independently testable. The desktop applies this in the admission turn.
        if not hasattr(self,'tree') or self.lib is None:return
        if rebuild and presentation is None:presentation=self._native_presentation()
        removed=self._native_pending_deletions(presentation=presentation)
        identity=(str(self.lib.source),self.lib.source_library_id) if self.lib else None
        expanded=(set(getattr(self,'_pending_native_expansion',set()))
                  if identity==getattr(self,'_pending_native_identity',None) else set())
        self._pending_native_identity=identity
        present=set();now_expanded=set()
        def remember(parent):
            for i in range(parent.childCount()):
                item=parent.child(i)
                identity=item.data(0,RB_NODE_ROLE);present.add(identity)
                if item.isExpanded():now_expanded.add(identity)
                remember(item)
        remember(self.tree.invisibleRootItem())
        expanded.difference_update(present);expanded.update(now_expanded)
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
                updated=next((p for p in presentation.playlists if p.pid==active),None)
                if updated:self._view_key='rb:'+updated.display_path
            self._populate_tree(engine.summary(self.lib),presentation=presentation)
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

    def _native_reorder(self,identity,anchor_id,after):
        reason=self.playlist_controller.reorder_reason()
        if reason:self._flash_status(reason,8);return False
        item=self._find_tree_item('',identity)
        if item is None or item.data(0,PINNED_TREE_ROLE):return False
        target=self._native_node(identity);anchor=self._native_node(anchor_id)
        if not target or not anchor or target['ParentID']!=anchor['ParentID']:return False
        siblings=sorted((r for r in self._native_presentation().source_playlist_catalog['nodes']
                         if r['ParentID']==target['ParentID'] and r['rb_local_deleted']==0),key=lambda r:r['Seq'])
        original=[r['ID'] for r in siblings];updated=[value for value in original if value!=identity]
        if anchor_id not in updated:return False
        updated.insert(updated.index(anchor_id)+int(after),identity)
        if original==updated:return True
        return self._native_request('reorder',identity=identity,anchor_id=anchor_id,after=after)

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
        item=self.tree.playlist_icon_item_at(pos)
        menu=self._playlist_icon_menu(self.tree,item) if item is not None else None
        if menu is not None:
            exec_transient_menu(menu, self.tree.viewport().mapToGlobal(pos));return
        exec_transient_menu(self._native_menu(self.tree.itemAt(pos)), self.tree.viewport().mapToGlobal(pos))

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
        commands=command_requests(request)
        affected={t['track_id'] for command in commands for t in command['tracks']}
        if request['operation']=='delete':
            affected|={lib.tracks_by_id[tid].native_id for p in lib.playlists if p.pid not in active for tid in p.keys if tid in lib.tracks_by_id}
        for tid in affected:
            track=native_tracks.get(tid)
            if track and track.source_uuid:pins[tid]=track.source_uuid
        current=getattr(self,'_view_key','') or ''
        prior=(self._native_view_playlist(current[3:]).pid if current.startswith('rb:') and self._native_view_playlist(current[3:]) else None)
        lib.playlists=result;lib.source_playlist_catalog=catalog
        for command in commands:
            if command['operation']!='remove':continue
            bag=self.removals.get(command['playlist_id'],{})
            for track in command['tracks']:
                current_track=native_tracks.get(track['track_id'])
                bag.pop(current_track.track_id if current_track else track['track_id'],None)
            if not bag:self.removals.pop(command['playlist_id'],None)
        if prior:
            updated=next((p for p in result if p.pid==prior),None)
            self._view_key='rb:'+updated.display_path if updated else 'sb:Collection'
        self._apply_settings_to_lib(invalidate_rows=False,configure_artwork=False)
        self._sync_playlists_cache=None

    def _native_refresh_view(self,request):
        self._populate_tree(engine.summary(self.lib))
        if (getattr(self,'_view_key','') or '').startswith('rb:'):self._reload_current_view()
        self._cache_library()
        # Ordinary device polling observes the invalidated playlist inventory.
        # A source playlist save neither changes Collection nor its artwork.
        if any(command['operation'] in ('delete','remove','rename') for command in command_requests(request)):self._refresh_graveyard()

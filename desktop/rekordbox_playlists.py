"""Native playlist gestures and a durable FIFO on the shared source worker lane."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict, deque
from dataclasses import replace
import json
import logging
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from soundbox import engine
from soundbox.library import Playlist
from soundbox.rekordbox_playlist_plan import smart_parent_reason, smart_tag_rule
from soundbox.rekordbox_playlist_store import (PlaylistStore, load_authority, request_for, validate_request,
    command_requests, validate_collection_pins)
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed
from .choices import Menu, exec_transient_menu
from .trees import (ARTISTS_ROLE, COLLECTION_ROLE, CUSTOM_ROLE, NATIVE_TREE_ROLE,
                    PATH_ROLE, RB_NODE_ROLE, PINNED_TREE_ROLE, sidebar_choice_icon)

QUEUE='rekordbox_playlist_edits'
LOG=logging.getLogger('soundbox')
_REMOVAL_OVERLAY='soundbox-playlist-removal-overlay-v1'


def _validate_removal_overlay(proof,request):
    allowed={(command['playlist_id'],command['playlist_uuid'],track['track_id'],track['uuid'])
             for command in command_requests(request) if command['operation']=='remove' for track in command['tracks']}
    if (not allowed or not isinstance(proof,dict) or set(proof)!={'format','entries'}
            or proof['format']!=_REMOVAL_OVERLAY or not isinstance(proof['entries'],list)
            or len(proof['entries'])>32768):raise ValueError('Invalid captured playlist removal overlay')
    seen=set()
    for row in proof['entries']:
        if (not isinstance(row,dict) or set(row)!={'playlist_id','playlist_uuid','native_id','track_uuid','local_id','value'}
                or any(type(value) is not str or '\0' in value or len(value)>4096 for value in row.values())
                or not row['local_id'] or (row['playlist_id'],row['playlist_uuid'],row['native_id'],row['track_uuid']) not in allowed
                or (row['playlist_id'],row['local_id']) in seen):raise ValueError('Captured removal overlay identity changed')
        seen.add((row['playlist_id'],row['local_id']))


def _capture_removal_overlay(request,removals,library,selected_tracks=()):
    """Capture only selected keys, using already-selected logical aliases."""
    aliases=defaultdict(dict)
    for track in selected_tracks:aliases[(track.native_id,track.source_uuid)][track.track_id]=None
    entries=[];seen=set();captured=set()
    for command in command_requests(request):
        if command['operation']!='remove':continue
        bag=removals.get(command['playlist_id'],{})
        if not isinstance(bag,dict):raise ValueError('Saved playlist removal overlay needs attention')
        for pin in command['tracks']:
            group=(command['playlist_id'],command['playlist_uuid'],pin['track_id'],pin['uuid'])
            if group in captured:continue
            captured.add(group)
            current=library.tracks_by_id.get(pin['track_id'])
            local=aliases.get((pin['track_id'],pin['uuid']),{})
            if not local and current and current.source_uuid==pin['uuid']:local={current.track_id:None}
            for key in dict.fromkeys((pin['track_id'],*local)):
                if key not in bag or (command['playlist_id'],key) in seen:continue
                entries.append(dict(playlist_id=command['playlist_id'],playlist_uuid=command['playlist_uuid'],
                    native_id=pin['track_id'],track_uuid=pin['uuid'],local_id=key,value=bag[key]))
                seen.add((command['playlist_id'],key))
    proof=dict(format=_REMOVAL_OVERLAY,entries=entries);_validate_removal_overlay(proof,request)
    return proof


def _ack_removal_overlay(removals,proof,targets,library):
    """Clear exact captured values only; never mutate an older owner in place."""
    result=removals;changed={}
    for row in proof['entries']:
        if targets.get(row['playlist_id'])!=row['playlist_uuid']:continue
        track=library.tracks_by_id.get(row['local_id'])
        if (track and (track.native_id,track.source_uuid)!=(row['native_id'],row['track_uuid'])
                or track is None and row['local_id']!=row['native_id']):continue
        bag=result.get(row['playlist_id'],{})
        if not isinstance(bag,dict) or bag.get(row['local_id'])!=row['value']:continue
        if result is removals:result=dict(removals)
        if row['playlist_id'] not in changed:changed[row['playlist_id']]=dict(bag)
        changed[row['playlist_id']].pop(row['local_id'],None)
        if changed[row['playlist_id']]:result[row['playlist_id']]=changed[row['playlist_id']]
        else:result.pop(row['playlist_id'],None)
    return result


def _intent_xml_clocks(request, clocks, *, advance=False):
    """Keep one unstarted FIFO's child and shared-parent clocks in order."""
    for command in command_requests(request):
        identity=command['playlist_id']
        if advance:
            command['xml_timestamp']=max(command['xml_timestamp'],clocks.get(identity,0)+1)
        clocks[identity]=max(clocks.get(identity,0),command['xml_timestamp'])
        if command['operation']=='create_smart' and command['parent']!='root':
            parent=command['parent'];bundle=command['smart_clocks']
            if advance:
                bundle['parent_xml_timestamp']=max(bundle['parent_xml_timestamp'],clocks.get(parent,0)+1)
            clocks[parent]=max(clocks.get(parent,0),bundle['parent_xml_timestamp'])


def _verified_smart_readback(library, catalog, request, source, library_id):
    if (library is None or library.source_kind != 'rekordbox-db'
            or (str(library.source), library.source_library_id) != (source, library_id)
            or library.source_playlist_catalog.get('revision') != catalog['revision']):
        return False
    node = next((row for row in catalog['nodes'] if row['ID'] == request['playlist_id']), None)
    if (node is None or node.get('UUID') != request['playlist_uuid'] or node.get('Attribute') != 4
            or node.get('rb_local_deleted') != 0 or node.get('Name') != request['name']
            or node.get('ParentID') != request['parent']
            or not any(playlist.pid == request['playlist_id'] for playlist in library.playlists)):
        return False
    try:
        expected_rule = smart_tag_rule(request['playlist_id'], [tag['id'] for tag in request['tags']], match='any')
    except (KeyError, TypeError, ValueError):
        return False
    if node.get('SmartList') != expected_rule:
        return False
    fresh_node = next((row for row in library.source_playlist_catalog.get('nodes', [])
                       if row['ID'] == request['playlist_id']), None)
    if fresh_node != node:
        return False
    fresh_tags = {(tag.tag_id, tag.source_uuid, tag.name, tag.category, tag.category_id, tag.category_uuid)
                  for tag in library.source_tags}
    return all((tag['id'], tag['uuid'], tag['name'], tag['category'], tag['category_id'], tag['category_uuid'])
               in fresh_tags for tag in request['tags'])


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
                if 'removal_overlay' in job:_validate_removal_overlay(job['removal_overlay'],job['request'])
                if 'collection_pins' in job:
                    if job['request']['operation'] not in ('delete','delete_tree'):raise ValueError('Unknown Collection retention intent')
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

    def smart_reason(self):
        reason = self.reason()
        if reason or self.factory:
            return reason
        from soundbox.rekordbox_playlist_store import smart_creation_reason
        lib = self.window.lib
        authority = load_authority(self.tags.data_root / 'rekordbox-playlist-authority.json',
                                   lib.source, lib.source_library_id)
        return smart_creation_reason(authority)

    def smart_delete_reason(self, identity=None):
        reason = self.reason()
        if reason:
            return reason
        from soundbox.rekordbox_playlist_store import (smart_deletion_reason,
            SMART_DELETE_PROFILE, SMART_LOCAL_DELETE_PROFILE)
        lib = self.window.lib
        target = next((node for node in lib.source_playlist_catalog['nodes'] if node['ID'] == identity), None)
        profile = (SMART_LOCAL_DELETE_PROFILE if target and target.get('rb_local_synced') == 1
                   else SMART_DELETE_PROFILE)
        authority = (load_authority(self.tags.data_root / 'rekordbox-playlist-authority.json',
                                   lib.source, lib.source_library_id) if not self.factory else None)
        reason = smart_deletion_reason(authority, profile=profile)
        if reason or identity is None:
            return reason
        from soundbox.rekordbox_playlist_plan import smart_delete_reason
        return smart_delete_reason(lib.source_playlist_catalog['nodes'], identity)

    def enqueue(self, request, *, selected_tracks=()):
        started=time.monotonic()
        validate_request(request)
        reason=(self.reorder_reason() if request['operation']=='reorder'
                else self.smart_reason() if request['operation']=='create_smart'
                else self.smart_delete_reason(request['playlist_id']) if request['operation']=='delete_smart' else self.reason())
        if reason:self.window._flash_status(reason,10);return False
        if request['operation'] == 'delete_tree':
            from soundbox.rekordbox_playlist_store import tree_bindings
            try:
                if tree_bindings(self.window._native_presentation().source_playlist_catalog,request['playlist_id']) != request['descendants']:
                    raise WriteProofError('The selected folder descendants changed; choose the deletion again')
            except (ValueError,KeyError,WriteProofError) as exc:
                self.window._flash_status(str(exc),10);return False
            for child in command_requests(request):
                if child['operation'] == 'delete_smart':
                    reason = self.smart_delete_reason(child['playlist_id'])
                    if reason:self.window._flash_status(reason,10);return False
        if request['operation']=='create_smart' and smart_parent_reason(
                self.window.lib.source_playlist_catalog['nodes'], request['parent']):
            self.window._flash_status('This folder is not supported for smart playlist creation yet. Choose another folder.', 10)
            return False
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
            _intent_xml_clocks(pending['request'],clocks)
        _intent_xml_clocks(request,clocks,advance=True)
        try:validate_request(request)
        except WriteProofError as exc:
            self.window._flash_status(str(exc),10);return False
        w=self.window;missing=object();old=w.settings.get(QUEUE,missing)
        job=dict(source=str(w.lib.source),library_id=w.lib.source_library_id,request=request,error='',retry=False)
        if any(command['operation']=='remove' for command in command_requests(request)):
            try:job['removal_overlay']=_capture_removal_overlay(request,getattr(w,'removals',{}),w.lib,selected_tracks)
            except (ValueError,KeyError,TypeError) as exc:w._flash_status(str(exc),10);return False
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
        if (self.tags.source_job_owned or w._refresh_worker is not None or w._state_maintenance
                or w._device_operation_owned()):
            self.timer.start();return
        if self.reason():return
        if time.monotonic()<self.not_before:
            self.timer.start();return
        job=deepcopy(self.pending[0]);lib=w.lib
        if job['source']!=str(lib.source) or job['library_id']!=lib.source_library_id:
            self.fail('Pending playlist edit belongs to a different rekordbox library', job);return
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
            if job['request']['operation'] in ('delete','delete_tree'):
                if not hasattr(store,'prepare_collection_pins'):
                    raise WriteProofError('Native playlist deletion needs exact Collection retention capture')
                if 'collection_pins' not in job:
                    return dict(collection_pins=store.prepare_collection_pins(job['request']))
                catalog=store.apply(job['request'],collection_pins=job['collection_pins'])
            else:
                catalog = store.apply(job['request'])
            # A replay can return a fresh catalog containing independent native
            # edits. Its operation receipt alone cannot rebase our queued tail.
            # Capture the exact committed edge on this worker before GUI ACK.
            transition = ((job['request']['revision'], catalog['revision'])
                          if job['request']['revision'] == catalog['revision'] else None)
            proof = getattr(store, 'committed_transition', None)
            if transition is None and proof is not None:
                transition = proof(job['request'])
            result = dict(catalog=catalog, transition=transition)
            if 'removal_overlay' in job:
                wanted={row['playlist_id'] for row in job['removal_overlay']['entries']};targets={}
                for node in catalog['nodes']:
                    if node['ID'] in wanted:
                        targets[node['ID']]=(node['UUID'] if node['ID'] not in targets and node.get('rb_local_deleted')==0
                                             and node.get('Attribute')==0
                                             else None)
                result['removal_overlay_targets']=targets
            if job['request']['operation'] == 'create_smart':
                result['library'] = self.tags._loader(Path(lib.source))
            return result
        def completed(worker):
            if worker.error:self.fail(worker.error, job);return
            if worker.result.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Playlist edit pending — quit rekordbox to save')
                if not self.closing:self.timer.start()
                return
            if (not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id'])
                    or not self.pending or self.pending[0]['request']!=job['request']):
                self.fail('Saved in the original rekordbox library; switch back to finish this edit', job);return
            if 'collection_pins' in worker.result:
                previous=dict(w.settings)
                try:
                    pins=worker.result['collection_pins']
                    _remember_collection_pins(w.settings,job['library_id'],pins)
                    queue=list(self.pending);queue[0]={**queue[0],'collection_pins':pins};w.settings[QUEUE]=queue
                    if not w._save_state():raise OSError('Could not persist native Collection retention; playlist deletion was not started')
                except (OSError,ValueError,WriteProofError) as exc:
                    w.settings.clear();w.settings.update(previous);self.fail(str(exc), job);return
                self.not_before=0;self.schedule();return
            if 'retry_request' in worker.result:
                previous=w.settings.get(QUEUE,[]);jobs=list(previous)
                jobs[0]={**jobs[0],'request':worker.result['retry_request'],'error':'','retry':False}
                if 'removal_overlay' in jobs[0]:
                    try:_validate_removal_overlay(jobs[0]['removal_overlay'],jobs[0]['request'])
                    except (ValueError,KeyError,TypeError) as exc:self.fail(str(exc),job);return
                if worker.result['retry_request']!=job['request']:jobs[0].pop('collection_pins',None)
                w.settings[QUEUE]=jobs
                if not w._save_state():
                    w.settings[QUEUE]=previous;self.fail('Could not save the renewed playlist intent', job);return
                self.not_before=0;self.schedule();return
            before=w.settings.get(QUEUE,[]);tail=list(before[1:])
            new_catalog=worker.result['catalog']
            if not w.lib or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']:
                self.fail('Saved in the original rekordbox library; switch back to finish this edit', job);return
            smart_library = worker.result.get('library')
            if job['request']['operation'] == 'create_smart' and not _verified_smart_readback(
                    smart_library, new_catalog, job['request'], job['source'], job['library_id']):
                self.fail('Smart playlist readback could not be verified; the intention is preserved', job);return
            # Rebase only this FIFO's own still-unstarted edits. External source
            # changes remain conflicts; a worker never silently retargets them.
            tail_clocks={}
            _intent_xml_clocks(job['request'],tail_clocks)
            causal = worker.result.get('transition') == (job['request']['revision'], new_catalog['revision'])
            for index,later in enumerate(tail):
                if (later['source']==job['source'] and later['library_id']==job['library_id']
                        and later['request']['revision']==job['request']['revision']):
                    later=deepcopy(later);tail[index]=later
                    if not causal:
                        later['error']='Native library changed outside this saved operation; review and retry this queued edit'
                        continue
                    if later['request']['operation'] in ('create_smart', 'delete_smart', 'delete_tree'):
                        from soundbox.rekordbox_playlist_store import (renew_smart_request,
                            renew_smart_delete_request,renew_tree_delete_request)
                        try:
                            renew={'create_smart':renew_smart_request,'delete_smart':renew_smart_delete_request,
                                   'delete_tree':renew_tree_delete_request}[later['request']['operation']]
                            later['request']=renew(later['request'],new_catalog)
                        except (ValueError,WriteProofError) as exc:
                            later['error']=str(exc)
                    else:
                        later['request']['revision']=new_catalog['revision']
                        for command in command_requests(later['request']):command['revision']=new_catalog['revision']
                    _intent_xml_clocks(later['request'],tail_clocks,advance=True)
                    validate_request(later['request'])
                elif later['source']==job['source'] and later['library_id']==job['library_id']:
                    _intent_xml_clocks(later['request'],tail_clocks)
            w.settings[QUEUE]=tail
            prior_removals=getattr(w,'removals',None)
            prior_masks=w.lib.removed_by_playlist
            if 'removal_overlay' in job and prior_removals is not None:
                w.removals=_ack_removal_overlay(prior_removals,job['removal_overlay'],
                    worker.result.get('removal_overlay_targets',{}),w.lib)
            if smart_library is None:
                w._native_apply_catalog(new_catalog,job['request'])
            if not w._save_state():
                w.settings[QUEUE]=before
                if prior_removals is not None:w.removals=prior_removals
                w.lib.removed_by_playlist=prior_masks
                self.fail('Saved in rekordbox; SoundBox state could not be saved. Retry after fixing storage.', job);return
            self.not_before=0
            if not self.closing:
                if smart_library is not None:w._publish_created_library(smart_library)
                else:w._native_refresh_view(job['request'])
            self.status('Updating playlist…' if self.pending else '')
            finish=getattr(w,'_finish_edit_status',None)
            if not self.pending and finish:finish(quiet=True)
            self.schedule()
        self.tags.run_playlist_job(work,completed)

    def fail(self, message, job=None):
        # Synchronous UI reports refer to the current head. Worker callbacks
        # always pass their captured job so late results cannot target a new one.
        if job is None:
            if not self.pending or self.queue_error():
                self.window._flash_status(message,12);return
            job=deepcopy(self.pending[0])
        self.timer.stop();w=self.window
        jobs=list(self.pending)
        owned=next((index for index,current in enumerate(jobs)
                    if isinstance(current,dict)
                    and (current.get('source'),current.get('library_id'),current.get('request'))
                    == (job['source'],job['library_id'],job['request'])),None)
        if owned is not None:
            jobs[owned]={**jobs[owned],'error':message};w.settings[QUEUE]=jobs;w._save_state()
            if w.lib and (str(w.lib.source),w.lib.source_library_id)==(job['source'],job['library_id']):
                w._native_pending_changed(rebuild=True)
                w._native_reconcile_source()
            self.status('Rekordbox playlist edit needs attention — '+message)
        else:w._flash_status('Original playlist operation: '+message,12)

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
    @staticmethod
    def _native_row_identity(row):
        pin = row.get('_native_membership')
        if isinstance(pin, dict):
            return ('native-membership', *(pin.get(field) for field in
                ('source', 'library_id', 'playlist_id', 'playlist_uuid', 'ID', 'UUID', 'ContentID', 'track_uuid')))
        return ('track', row.get('id'))

    def _native_membership_rows(self, playlist, rows):
        """Give each ordinary browser occurrence its own immutable source pins.

        Track display dictionaries may be cached/shared. Copy each occurrence
        before attaching view-specific identity, including repeated songs.
        """
        library = self._native_presentation()
        if not library or library.source_kind != 'rekordbox-db':
            return rows
        catalog = library.source_playlist_catalog
        target = next((node for node in catalog.get('nodes', []) if node['ID'] == playlist.pid), None)
        if not target or target['Attribute'] != 0:
            return rows
        aliases = {track.native_id: track.track_id for track in library.tracks_by_id.values()}
        members = defaultdict(deque)
        for member in sorted((row for row in catalog.get('members', [])
                              if row['PlaylistID'] == playlist.pid and row['rb_local_deleted'] == 0),
                             key=lambda row: row['TrackNo']):
            members[aliases.get(member['ContentID'], member['ContentID'])].append(member)
        result = []
        for row in rows:
            copied = dict(row)
            copied.pop('_native_membership', None)
            choices = members.get(row.get('id'))
            if choices:
                member = choices.popleft()
                track = library.tracks_by_id.get(row.get('id'))
                copied['_native_membership'] = dict(source=str(library.source), library_id=library.source_library_id,
                    playlist_id=playlist.pid, playlist_uuid=target['UUID'], ID=member['ID'], UUID=member['UUID'],
                    PlaylistID=member['PlaylistID'], ContentID=member['ContentID'],
                    track_uuid=track.source_uuid if track else None)
            result.append(copied)
        return result

    def _native_capture_remove(self, display_path, ids, rows):
        """Capture clicked occurrences before a menu's nested event loop."""
        library = self._native_presentation()
        playlist = self._native_view_playlist(display_path)
        if not library or playlist is None or rows is None or len(rows) != len(ids):
            raise WriteProofError('Refresh the playlist before removing its selected entries')
        node = next((row for row in library.source_playlist_catalog['nodes'] if row['ID'] == playlist.pid), None)
        if not node or node['Attribute'] != 0:
            raise WriteProofError('Smart-playlist membership follows its rules; edit My Tags instead')
        pins = []
        tracks = []
        for identity, row in zip(ids, rows, strict=True):
            pin = row.get('_native_membership')
            track = library.tracks_by_id.get(identity)
            if (row.get('id') != identity or not track or not isinstance(pin, dict)
                    or (pin.get('source'), pin.get('library_id'), pin.get('playlist_id'), pin.get('playlist_uuid'))
                    != (str(library.source), library.source_library_id, playlist.pid, node['UUID'])):
                raise WriteProofError('The selected playlist entries changed; refresh before removing them')
            pins.append(deepcopy(pin)); tracks.append(track)
        request = request_for(library.source_playlist_catalog, 'remove', identity=playlist.pid,
                              tracks=tracks, memberships=pins)
        return dict(source=str(library.source), library_id=library.source_library_id,
                    track_ids=list(ids), memberships=pins, request=request)

    def _native_submit_remove(self, captured):
        library = self._native_presentation()
        if not library or (str(library.source), library.source_library_id) != (captured['source'], captured['library_id']):
            self._flash_status('The library changed; choose the playlist entries again', 10);return False
        request = captured['request']
        node = next((row for row in library.source_playlist_catalog['nodes'] if row['ID'] == request['playlist_id']), None)
        tracks = [library.tracks_by_id.get(identity) for identity in captured['track_ids']]
        if (not node or node['UUID'] != request['playlist_uuid'] or node['Attribute'] != 0
                or any(track is None or track.native_id != selected['track_id'] or track.source_uuid != selected['uuid']
                       for track, selected in zip(tracks, request['tracks'], strict=True))):
            self._flash_status('The selected playlist or songs changed; choose them again', 10);return False
        return self._native_request('remove', identity=node['ID'], tracks=tracks,
                                    memberships=captured['memberships'])

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

    def _native_node_scope(self, row):
        return (str(self.lib.source),self.lib.source_library_id,row['ID'],row['UUID'],row['Attribute']) if self.lib and row else None

    def _native_parent_scope(self, parent):
        if not self.lib:return None
        row=self._native_node(parent) if parent!='root' else None
        if parent!='root' and (row is None or row['Attribute']!=1 or row['rb_local_deleted']!=0):return None
        return str(self.lib.source),self.lib.source_library_id,parent,row['UUID'] if row else ''

    def _native_request(self, operation, **kwargs):
        reason=self.playlist_controller.reason()
        if reason:self._flash_status(reason,10);return False
        # Only target identity/presentation comes from the projected view. The
        # captured revision remains the verified source baseline; FIFO rebase
        # renews it after preceding accepted operations commit.
        try:request=request_for(self._native_presentation().source_playlist_catalog,operation,**kwargs)
        except (ValueError,KeyError,WriteProofError) as exc:self._flash_status(str(exc),10);return False
        accepted=(self.playlist_controller.enqueue(request,selected_tracks=kwargs.get('tracks',()))
                  if operation=='remove' else self.playlist_controller.enqueue(request))
        if accepted and operation=='create' and hasattr(self,'tree'):
            item=self._find_tree_item('',request['playlist_id'])
            if item:
                if item.data(0,PATH_ROLE):self.tree.setCurrentItem(item)
                else:item.setExpanded(True)
        return accepted

    def _native_new(self, parent='root', *, folder=False, expected_parent=None):
        reason=self.playlist_controller.reason()
        if reason:self._flash_status(reason,10);return
        scope=self._native_parent_scope(parent)
        if scope is None or expected_parent is not None and scope!=expected_parent:
            self._flash_status('The destination folder changed; open its menu again',10);return
        label='Folder' if folder else 'Playlist'
        name,ok=QInputDialog.getText(self,'New rekordbox '+label,'Name:')
        name=name.strip()
        if not ok or not name:return
        if self._native_parent_scope(parent)!=scope:
            self._flash_status('The destination changed while naming; choose the folder again',10);return
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

    def _native_rename(self, identity, *, expected=None):
        row=self._native_node(identity)
        if row is None:return
        scope=self._native_node_scope(row)
        if expected is not None and scope!=expected:
            self._flash_status('The playlist changed; open its menu again',10);return
        if row['Attribute'] not in (0,1):
            self._flash_status('Smart-playlist names are not editable from this rename action',10);return
        item=self._find_tree_item('',identity=identity)
        self.tree.rename_inline(item,row['Name'],lambda name:self._native_rename_commit(identity,row['Name'],name,expected=scope))

    def _native_rename_commit(self, identity, previous, name, *, expected=None):
        row=self._native_node(identity)
        if row is None or row['Name']!=previous or expected is not None and self._native_node_scope(row)!=expected:
            self._flash_status('Playlist changed before renaming; try again',8);return
        name=name.strip()
        if any(other['ID']!=identity and other['ParentID']==row['ParentID'] and other['Name']==name
               and other['rb_local_deleted']==0 for other in self._native_presentation().source_playlist_catalog['nodes']):
            self._flash_status('A playlist or folder with that name already exists here',8);return
        if name and name!=row['Name']:
            self._native_request('rename',identity=identity,name=name)

    def _native_delete(self, identity, *, expected=None):
        row=self._native_node(identity)
        if row is None:return
        scope=self._native_node_scope(row)
        if expected is not None and scope!=expected:
            self._flash_status('The playlist changed; open its menu again',10);return
        operation = 'delete_smart' if row['Attribute'] == 4 else 'delete'
        reason = (self.playlist_controller.smart_delete_reason(identity) if operation == 'delete_smart'
                  else self.playlist_controller.reason())
        if reason:
            self._flash_status(reason, 10);return
        captured = None
        if row['Attribute'] == 1:
            from soundbox.rekordbox_playlist_store import tree_bindings,tree_delete_for
            try:
                catalog=self._native_presentation().source_playlist_catalog
                bindings=tree_bindings(catalog,identity)
                if any(binding['attribute']==4 for binding in bindings):
                    captured=tree_delete_for(catalog,identity)
                    for command in command_requests(captured):
                        if command['operation']=='delete_smart':
                            reason=self.playlist_controller.smart_delete_reason(command['playlist_id'])
                            if reason:self._flash_status(reason,10);return
            except (ValueError,KeyError,WriteProofError) as exc:
                self._flash_status(str(exc),10);return
        kind='folder and its playlists' if row['Attribute']==1 else 'playlist'
        if QMessageBox.question(self,'Delete from rekordbox',
                f'Delete the rekordbox {kind} “{row["Name"]}”?\nSongs remain in Collection and audio files are preserved.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            fresh=self._native_node(identity)
            if self._native_node_scope(fresh)!=scope:
                self._flash_status('The playlist changed during confirmation; choose it again',10);return
            if captured is not None:
                from soundbox.rekordbox_playlist_store import renew_tree_delete_request
                try:request=renew_tree_delete_request(captured,self._native_presentation().source_playlist_catalog)
                except (ValueError,KeyError,WriteProofError) as exc:
                    self._flash_status(str(exc),10);return
                self.playlist_controller.enqueue(request)
            else:self._native_request(operation,identity=identity)

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
        scope=self._native_node_scope(row)
        existing={r['ContentID'] for r in self._native_presentation().source_playlist_catalog['members']
                  if r['PlaylistID']==identity and r['rb_local_deleted']==0}
        seen=set(existing);duplicates=[];unique=[]
        for track in tracks:
            if track.native_id in seen:duplicates.append(track.track_id)
            else:unique.append(track);seen.add(track.native_id)
        allow=bool(duplicates) and self._confirm_duplicate_add(row['Name'],duplicates)
        if (self._native_node_scope(self._native_node(identity))!=scope
                or any((fresh:=self.lib.tracks_by_id.get(track.track_id)) is None
                       or (fresh.native_id,fresh.source_uuid)!=(track.native_id,track.source_uuid) for track in tracks)):
            self._flash_status('The playlist or tracks changed during confirmation; select them again',10);return
        selected=tracks if allow else unique
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
        scope=self._native_node_scope(row)
        parent_scope=self._native_parent_scope(parent)
        reason=self.playlist_controller.reason()
        for label,callback,available in (
            ('New Playlist',lambda:self._native_new(parent,expected_parent=parent_scope),True),
            ('New Smart Playlist',lambda:self._new_smart_playlist(parent),True),
            ('New Folder',lambda:self._native_new(parent,folder=True,expected_parent=parent_scope),True),
            ('Rename',lambda:self._native_rename(identity,expected=scope),bool(row and row['Attribute'] in (0,1))),
            ('Delete',lambda:self._native_delete(identity,expected=scope),bool(row and row['Attribute'] in (0,1,4)))):
            action_reason = (self.playlist_controller.smart_reason() if label == 'New Smart Playlist'
                             else self.playlist_controller.smart_delete_reason(identity)
                             if label == 'Delete' and row and row['Attribute'] == 4 else reason)
            if label=='Delete' and row and row['Attribute']==1 and not action_reason:
                from soundbox.rekordbox_playlist_store import tree_bindings
                try:
                    bindings=tree_bindings(self._native_presentation().source_playlist_catalog,identity)
                    for binding in bindings:
                        if binding['attribute']==4:
                            action_reason=self.playlist_controller.smart_delete_reason(binding['id'])
                            if action_reason:break
                except (ValueError,KeyError,WriteProofError) as exc:action_reason=str(exc)
            action=menu.addAction(label,callback);action.setEnabled(not action_reason and available)
            if action_reason:action.setToolTip(action_reason)
        if self.playlist_controller.pending:
            menu.addSeparator();menu.addAction('Retry pending edits',self.playlist_controller.retry)
            discard=menu.addAction('Discard pending edits',self.playlist_controller.discard)
            discard.setEnabled(not self.tag_controller.owned)
        definitions = getattr(self, 'tag_definition_controller', None)
        if definitions and definitions.pending:
            menu.addSeparator()
            retry = menu.addAction('Retry tag changes', definitions.retry)
            retry.setEnabled(not self.tag_controller.owned)
            discard = menu.addAction('Discard pending tag changes', definitions.discard)
            discard.setEnabled(not self.tag_controller.owned)
        tags=self.tag_controller
        physical=bool(getattr(tags,'busy',False) or getattr(tags,'checking',False)
                      or getattr(getattr(tags,'owner',None),'pending',0))
        metadata=getattr(self,'metadata_controller',None)
        if metadata and (metadata.pending or getattr(metadata,'migration_pending',False)
                         or getattr(metadata,'retirement_pending',False)):
            menu.addSeparator()
            retry=menu.addAction('Retry metadata saves',metadata.retry);retry.setEnabled(not physical)
            error=metadata.pending[0].get('error','') if metadata.pending else metadata.queue_error()
            if error:retry.setToolTip(error)
        if getattr(tags,'pending',[]):
            menu.addSeparator()
            retry=menu.addAction('Retry My Tag assignments',tags.retry);retry.setEnabled(not physical)
            if tags.pending[0].get('error'):retry.setToolTip(tags.pending[0]['error'])
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

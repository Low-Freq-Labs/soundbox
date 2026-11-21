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
from soundbox import playlist_creation_placement as creation_placement
from soundbox.library import Playlist
from soundbox.rekordbox_playlist_plan import smart_tag_rule, ordinary_request_reason
from soundbox.rekordbox_playlist_store import (PlaylistStore, load_authority, request_for, validate_request,
    command_requests, validate_collection_pins,CREATE_SMART_OPERATIONS,CREATE_ORDINARY_OPERATIONS)
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed
from .choices import Menu, exec_transient_menu
from .task_owner import DeferredCall
from .tag_controller import device_blocks_library_work
from .source_intent_admission import SourceIntentAdmission, SourceQueueAcknowledgement
from .inline_drafts import preserve_inline_drafts
from .trees import (ARTISTS_ROLE, COLLECTION_ROLE, CUSTOM_ROLE, NATIVE_TREE_ROLE,
                    PATH_ROLE, RB_NODE_ROLE, PINNED_TREE_ROLE, NATIVE_BINDING_ROLE,
                    native_playlist_binding, sidebar_choice_icon)

QUEUE='rekordbox_playlist_edits'
LOG=logging.getLogger('soundbox')
_REMOVAL_OVERLAY='soundbox-playlist-removal-overlay-v1'
_TAG_CREATION_DEPENDENCY_FIELDS=frozenset(('source','library_id','operation_id','tag_id',
    'tag_uuid','name','category_id','category_uuid'))


def _tag_binding(tag):
    return (tag.tag_id,tag.source_uuid,tag.name,tag.category,tag.category_id,tag.category_uuid)


def _validate_tag_creation_dependencies(dependencies,request,source,library_id):
    """A display-only future tag cannot replace a captured native prerequisite."""
    if (request['operation'] not in CREATE_SMART_OPERATIONS or not isinstance(dependencies,list)
            or not 1<=len(dependencies)<=len(request['tags'])):
        raise ValueError('Invalid pending smart-playlist tag creation dependencies')
    selections={tag['id']:tag for tag in request['tags']};seen=set();operations=set()
    for row in dependencies:
        if (not isinstance(row,dict) or set(row)!=_TAG_CREATION_DEPENDENCY_FIELDS
                or any(type(value) is not str or not value or len(value)>4096 for value in row.values())
                or (row['source'],row['library_id'])!=(source,library_id)
                or len(row['operation_id'])!=32
                or any(char not in '0123456789abcdef' for char in row['operation_id'])
                or row['tag_id'] in seen or row['operation_id'] in operations):
            raise ValueError('Pending smart-playlist tag creation identity changed')
        tag=selections.get(row['tag_id'])
        if tag is None or tuple(row[key] for key in ('tag_uuid','name','category_id','category_uuid'))!=\
                tuple(tag[key] for key in ('uuid','name','category_id','category_uuid')):
            raise ValueError('Pending smart-playlist tag creation differs from its selected tag')
        seen.add(row['tag_id']);operations.add(row['operation_id'])


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
        if command['operation'] in CREATE_SMART_OPERATIONS and command['parent']!='root':
            parent=command['parent'];bundle=command['smart_clocks']
            if advance:
                bundle['parent_xml_timestamp']=max(bundle['parent_xml_timestamp'],clocks.get(parent,0)+1)
            clocks[parent]=max(clocks.get(parent,0),bundle['parent_xml_timestamp'])
        if command['operation']=='reorder_smart' and command['parent']!='root':
            parent=command['parent'];clock=command['smart_parent_xml_timestamp']
            if advance:
                clock=max(clock,clocks.get(parent,0)+1,command['xml_timestamp'])
                command['smart_parent_xml_timestamp']=clock
            clocks[parent]=max(clocks.get(parent,0),clock)
        if command['operation'] in {'move_smart','move'} and command['parent']!='root':
            parent=command['parent'];bundle=command['node_move_clocks' if command['operation']=='move' else 'smart_move_clocks']
            clock=bundle['parent_xml_timestamp']
            if advance:
                clock=max(clock,clocks.get(parent,0)+1,command['xml_timestamp'])
                bundle['parent_xml_timestamp']=clock
            clocks[parent]=max(clocks.get(parent,0),clock)
        if command['operation'] in {'create_normalized','create_folder_normalized'} and command['parent']!='root':
            parent=command['parent'];bundle=command['normalized_create_clocks']
            clock=bundle['parent_xml_timestamp']
            if advance:
                clock=max(clock,clocks.get(parent,0)+1,command['xml_timestamp'])
                bundle['parent_xml_timestamp']=clock
            clocks[parent]=max(clocks.get(parent,0),clock)


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


def _catalog_collection_pins(library,catalog,request,groups):
    """Project the same exact Collection custody used by catalog publication."""
    native_tracks={track.native_id:track for track in library.tracks_by_id.values()}
    affected={track['track_id'] for command in command_requests(request) for track in command['tracks']}
    if request['operation']=='delete':
        active={row['ID'] for row in catalog['nodes'] if row['rb_local_deleted']==0}
        affected|={library.tracks_by_id[tid].native_id for playlist in library.playlists if playlist.pid not in active
                   for tid in playlist.keys if tid in library.tracks_by_id}
    captured={tid:native_tracks[tid].source_uuid for tid in affected
              if tid in native_tracks and native_tracks[tid].source_uuid}
    proposed={'rekordbox_collection_tracks':groups}
    _remember_collection_pins(proposed,library.source_library_id,captured)
    return proposed['rekordbox_collection_tracks']


class _PlaylistIntentAdmission(SourceIntentAdmission):
    def pump(self,*,allow_shutdown=False):
        if (self.pending and not self.active and not self.error and not self.stopping
                and not self._discarding and self._current()):
            for job in self.staged:
                try:ready,error=self.controller._tag_creation_admission(job)
                except (ValueError,KeyError,TypeError,WriteProofError) as exc:
                    self._failed(exc);return False
                if error:self._failed(RuntimeError(error));return False
                if not ready:self.timer.start(50);return False
        return super().pump(allow_shutdown=allow_shutdown)




class PlaylistEditController(QObject):
    def __init__(self, window, *, store_factory=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=store_factory;self.closing=False;self.not_before=0.0
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump)
        self._pump_wakeup=DeferredCall(self._scheduled_pump,self)
        self.tags.idle.connect(self.schedule);self.tags.changed.connect(self.schedule)
        self.admission=_PlaylistIntentAdmission(self,QUEUE,self._admission_changed,
            current_owner=lambda:getattr(window,'playlist_controller',self) is self)
        self.ack=SourceQueueAcknowledgement(self,QUEUE)

    @property
    def pending(self):
        value=self.window.settings.get(QUEUE,[])
        value=value if isinstance(value,list) else []
        return [*value,*self.admission.rows] if self.admission.staged else value

    @property
    def admission_pending(self):return self.ack.pending or self.admission.pending

    @property
    def admission_error(self):return self.ack.error or self.admission.error

    def flush_admission(self):return self.ack.flush() and self.admission.flush()

    def _admission_changed(self,outcome):
        w=self.window
        if outcome=='failed':
            self.status('Playlist intention NOT saved — '+self.admission.error)
            w._flash_status('Playlist intention NOT saved — retry or discard the pending intention',15)
        else:
            w._native_pending_changed(rebuild=True)
            if outcome=='discarded':self.status('');w._native_reconcile_source()
            else:self.status('Updating playlist…');self.schedule()

    def queue_error(self):
        jobs=self.pending if isinstance(self.window.settings.get(QUEUE,[]),list) else None
        try:
            if not isinstance(jobs,list) or len(jobs)>256:raise ValueError('Unknown queue')
            for job in jobs:
                if (not isinstance(job,dict) or type(job.get('source')) is not str
                        or type(job.get('library_id')) is not str or not job['library_id']
                        or type(job.get('error')) is not str):raise ValueError('Unknown intent')
                validate_request(job['request'])
                if 'creation_placement' in job:
                    creation_placement.validate(job['creation_placement'],job['request'],job['source'],job['library_id'])
                if 'removal_overlay' in job:_validate_removal_overlay(job['removal_overlay'],job['request'])
                if 'tag_creation_dependencies' in job:
                    _validate_tag_creation_dependencies(job['tag_creation_dependencies'],job['request'],
                        job['source'],job['library_id'])
                if 'collection_pins' in job:
                    if job['request']['operation'] not in ('delete','delete_tree'):raise ValueError('Unknown Collection retention intent')
                    validate_collection_pins(job['collection_pins'])
        except (KeyError,TypeError,ValueError,WriteProofError):
            return 'Saved native playlist edit queue is invalid; evidence preserved'
        return ''

    def reason(self):
        if self.queue_error():return self.queue_error()
        if self.ack.pending:return 'Finishing the playlist save acknowledgement; try again shortly'
        if self.admission.error:return 'The pending playlist intention needs retry or discard'
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

    def smart_append_reason(self):
        reason=self.reason()
        if reason or self.factory:return reason
        from soundbox.rekordbox_playlist_store import smart_append_creation_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return smart_append_creation_reason(authority)

    def smart_creation_reason(self):
        """The form can offer any independently admitted constructor."""
        return self.smart_reason() if self.smart_append_reason() and self.smart_normalized_reason() else ''

    def smart_normalized_reason(self):
        reason=self.reason()
        if reason:return reason
        from soundbox.rekordbox_playlist_store import smart_normalized_creation_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return smart_normalized_creation_reason(authority)

    def creation_catalog(self,catalog,parent='root'):
        """Capture constructors without treating display-only future rows as SQL history."""
        library=self.window.lib;verified=library.source_playlist_catalog
        if catalog['revision']!=verified['revision']:
            raise ValueError('The library changed before capturing this creation')
        actual={row['ID']:row for row in verified['nodes']}
        declared={}
        for job in self.pending:
            if (job.get('error') or (job['source'],job['library_id'],job['request']['revision'])!=
                    (str(library.source),library.source_library_id,verified['revision'])):break
            for request in command_requests(job['request']):
                if request['operation'] not in CREATE_ORDINARY_OPERATIONS|CREATE_SMART_OPERATIONS:continue
                if request['playlist_id'] in actual:continue
                captured=(request['playlist_uuid'],4 if request['operation'] in CREATE_SMART_OPERATIONS
                    else int(request['folder']),request['parent'],request['name'])
                if request['playlist_id'] in declared and declared[request['playlist_id']]!=captured:
                    raise ValueError('Pending playlist creation identity is ambiguous')
                declared[request['playlist_id']]=captured
        filtered=[]
        for row in catalog['nodes']:
            if row['ID'] in actual:
                if (row.get('UUID'),row.get('Attribute'))!=(actual[row['ID']]['UUID'],actual[row['ID']]['Attribute']):
                    raise ValueError('An existing playlist identity changed before creation')
                filtered.append(row);continue
            if (row.get('rb_local_deleted')!=0 or row['ID'] not in declared
                    or (row.get('UUID'),row.get('Attribute'),row.get('ParentID'),row.get('Name'))!=declared[row['ID']]):
                raise ValueError('An unverified future playlist cannot supply creation metadata')
            if row['ID']==parent:
                raise ValueError('Wait for this folder to finish creating before adding playlists inside it')
        if parent!='root' and not any(row['ID']==parent and row['Attribute']==1
                and row['rb_local_deleted']==0 for row in filtered):
            raise ValueError('The creation destination is no longer available')
        return {**catalog,'nodes':filtered}

    def smart_creation_operation(self,parent,catalog=None):
        from soundbox.rekordbox_playlist_store import (smart_append_creation_parent_reason,
            smart_creation_parent_reason,smart_normalized_creation_parent_reason,normalized_creation_required)
        catalog=catalog if catalog is not None else self.window.lib.source_playlist_catalog
        try:catalog=self.creation_catalog(catalog,parent)
        except (ValueError,KeyError,TypeError) as exc:return '',str(exc)
        try:root_gap=normalized_creation_required(catalog,'root')
        except (ValueError,KeyError,TypeError) as exc:return '',str(exc)
        old_parent=smart_creation_parent_reason(catalog,parent)
        old_reason=self.smart_reason()
        if not root_gap and not old_parent and not old_reason:return 'create_smart',''
        append_parent=smart_append_creation_parent_reason(catalog,parent)
        append_reason=self.smart_append_reason()
        if not root_gap and not append_parent and not append_reason:return 'create_smart_append',''
        normalized_parent=smart_normalized_creation_parent_reason(catalog,parent)
        normalized_reason=self.smart_normalized_reason()
        if not normalized_parent and not normalized_reason:return 'create_smart_normalized',''
        return '',normalized_parent or normalized_reason or old_parent or append_parent or old_reason or append_reason

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

    def smart_edit_reason(self,operation):
        reason=self.reason()
        if reason:return reason
        from soundbox.rekordbox_playlist_store import smart_editing_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return smart_editing_reason(authority,operation)

    def smart_move_reason(self):
        return self.smart_edit_reason('move_smart')

    def node_move_reason(self):
        reason=self.reason()
        if reason:return reason
        from soundbox.rekordbox_playlist_store import node_move_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return node_move_reason(authority)

    def normalized_creation_reason(self):
        reason=self.reason()
        if reason:return reason
        from soundbox.rekordbox_playlist_store import normalized_creation_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return normalized_creation_reason(authority)

    def normalized_folder_reason(self):
        reason=self.reason()
        if reason:return reason
        from soundbox.rekordbox_playlist_store import normalized_folder_creation_reason
        lib=self.window.lib
        authority=load_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        return normalized_folder_creation_reason(authority)

    def has_pending_tag_creation_dependency(self,operation_id):
        """Definition discard cannot orphan a staged or durable smart request."""
        if self.queue_error():return True
        return any(row.get('operation_id')==operation_id for job in self.pending
                   for row in job.get('tag_creation_dependencies',()) if isinstance(row,dict))

    def _capture_tag_creation_dependencies(self,request):
        if request['operation'] not in CREATE_SMART_OPERATIONS:return []
        definitions=getattr(self.window,'tag_definition_controller',None)
        capture=getattr(definitions,'creation_dependency',None)
        pending=getattr(definitions,'pending_creation',None)
        available=getattr(definitions,'available_tags',None)
        if not all(callable(method) for method in (capture,pending,available)):return []
        result=[];lib=self.window.lib
        choices=[_tag_binding(tag) for tag in available()]
        for tag in request['tags']:
            dependency=capture(tag['id'],tag['uuid'])
            if dependency is None:continue
            if choices.count(tuple(tag[key] for key in ('id','uuid','name','category','category_id','category_uuid')))!=1:
                raise ValueError('The selected pending My Tag changed; choose its current name again')
            creating=pending(tag['id'],tag['uuid'])
            if creating is None or creating.get('error'):
                raise ValueError('My Tag creation needs attention before creating this smart playlist')
            result.append(deepcopy(dependency))
        if result:_validate_tag_creation_dependencies(result,request,str(lib.source),lib.source_library_id)
        return result

    def _tag_creation_wait(self,job):
        """Wait for actual creation ACK/publication; never lend it a source lease."""
        dependencies=job.get('tag_creation_dependencies')
        if dependencies is None:return False,''
        request=job['request'];lib=self.window.lib
        _validate_tag_creation_dependencies(dependencies,request,job['source'],job['library_id'])
        definitions=getattr(self.window,'tag_definition_controller',None)
        pending=getattr(definitions,'pending_creation',None)
        capture=getattr(definitions,'creation_dependency',None)
        if not callable(pending) or not callable(capture):
            return False,'My Tag creation prerequisite cannot be verified; keep this smart-playlist intention'
        selections={tag['id']:tag for tag in request['tags']};waiting=False
        for dependency in dependencies:
            creating=pending(dependency['tag_id'],dependency['tag_uuid'])
            if creating is not None:
                if capture(dependency['tag_id'],dependency['tag_uuid'])!=dependency:
                    return False,'The original My Tag creation changed; review this smart-playlist intention'
                if creating.get('error'):
                    return False,'My Tag creation needs attention — '+creating['error']
                waiting=True;continue
            selected=selections[dependency['tag_id']]
            wanted=tuple(selected[key] for key in ('id','uuid','name','category','category_id','category_uuid'))
            matches=[_tag_binding(tag) for tag in lib.source_tags
                     if tag.tag_id==dependency['tag_id'] or tag.source_uuid==dependency['tag_uuid']]
            if matches!=[wanted]:
                return False,'The created My Tag is missing or changed; review this smart-playlist intention'
        if waiting:
            definitions.schedule()
        return waiting,''

    def _tag_creation_admission(self,job):
        """Persist the prerequisite before the dependent smart intention."""
        if 'tag_creation_dependencies' not in job:return True,''
        _waiting,error=self._tag_creation_wait(job)
        if error:return False,error
        definitions=getattr(self.window,'tag_definition_controller',None)
        durable=getattr(definitions,'creation_durable',None)
        for dependency in job['tag_creation_dependencies']:
            if definitions.pending_creation(dependency['tag_id'],dependency['tag_uuid']) is not None:
                if not callable(durable) or not durable(dependency):return False,''
        return True,''

    def presentation_tag_inputs(self,jobs):
        """Future tag names validate pending UI rows, never verified Library truth."""
        lib=self.window.lib;tags=list(lib.source_tags);visible=list(jobs)
        definitions=getattr(self.window,'tag_definition_controller',None)
        pending=getattr(definitions,'pending_creation',None)
        capture=getattr(definitions,'creation_dependency',None)
        available=getattr(definitions,'available_tags',None)
        choices=available() if callable(available) else []
        for index,job in enumerate(jobs):
            dependencies=job.get('tag_creation_dependencies')
            if not dependencies or job.get('error'):continue
            if (job['source'],job['library_id'])!=(str(lib.source),lib.source_library_id):continue
            selected={tag['id']:tag for tag in job['request']['tags']};additions=[];valid=True
            for dependency in dependencies:
                tag=selected[dependency['tag_id']]
                wanted=tuple(tag[key] for key in ('id','uuid','name','category','category_id','category_uuid'))
                creating=pending(tag['id'],tag['uuid']) if callable(pending) else None
                if creating is not None:
                    if (not callable(capture) or capture(tag['id'],tag['uuid'])!=dependency or creating.get('error')):
                        valid=False;break
                    matches=[choice for choice in choices if _tag_binding(choice)==wanted]
                    if len(matches)!=1:valid=False;break
                    additions.append(matches[0])
                elif sum(_tag_binding(choice)==wanted for choice in lib.source_tags)!=1:
                    valid=False;break
            if not valid:
                visible[index]={**job,'error':'The original new My Tag needs attention before this pending playlist'}
                continue
            for tag in additions:
                if not any(old.tag_id==tag.tag_id or old.source_uuid==tag.source_uuid for old in tags):tags.append(tag)
        return visible,tags

    def enqueue(self, request, *, selected_tracks=(),paired_settings=None,placement=None):
        started=time.monotonic()
        validate_request(request)
        reason=(self.smart_edit_reason(request['operation']) if request['operation'] in {'rename_smart','reorder_smart','move_smart'}
                else self.node_move_reason() if request['operation']=='move'
                else self.reorder_reason() if request['operation']=='reorder'
                else self.smart_reason() if request['operation']=='create_smart'
                else self.smart_append_reason() if request['operation']=='create_smart_append'
                else self.smart_normalized_reason() if request['operation']=='create_smart_normalized'
                else self.normalized_creation_reason() if request['operation']=='create_normalized'
                else self.normalized_folder_reason() if request['operation']=='create_folder_normalized'
                else self.smart_delete_reason(request['playlist_id']) if request['operation']=='delete_smart' else self.reason())
        if reason:self.window._flash_status(reason,10);return False
        try:tag_dependencies=self._capture_tag_creation_dependencies(request)
        except (ValueError,KeyError,TypeError) as exc:self.window._flash_status(str(exc),10);return False
        from .playlist_names import playlist_name_taken
        from soundbox import playlist_unification as merge
        from soundbox.playlist_names import normalize_playlist_name
        name_context=({'unification_state':paired_settings[merge.KEY]}
                      if paired_settings and merge.KEY in paired_settings else {})
        claimed = set()
        presentation = getattr(self.window, '_native_presentation', lambda:self.window.lib)()
        placement_proof=None
        if placement is not None:
            try:
                placement_proof=creation_placement.placement_for(request,placement,
                    self.window.lib.source,self.window.lib.source_library_id)
                if not creation_placement.anchor_matches(placement,presentation.source_playlist_catalog,
                        self.window.lib.source,self.window.lib.source_library_id):
                    raise ValueError('The playlist position changed; open its menu again')
                move_reason=self.node_move_reason()
                if move_reason:raise ValueError(move_reason)
            except (ValueError,KeyError,TypeError,WriteProofError) as exc:
                self.window._flash_status(str(exc),10);return False
        for command in command_requests(request):
            operation = command['operation']
            if operation in {'rename','reorder'}:
                reason=ordinary_request_reason(presentation.source_playlist_catalog,command)
                if reason:self.window._flash_status(reason,10);return False
            target = next((row for row in presentation.source_playlist_catalog.get('nodes', ())
                           if row['ID'] == command['playlist_id']), None)
            if (operation not in {*CREATE_ORDINARY_OPERATIONS, *CREATE_SMART_OPERATIONS, 'rename','rename_smart'} or command.get('folder')
                    or operation == 'rename' and target and target.get('Attribute') == 1):
                continue
            name = command['name']; normalized = normalize_playlist_name(name)
            if normalized in claimed or playlist_name_taken(self.window, name,
                    native_id=command['playlist_id'], native_uuid=command['playlist_uuid'], pending=self.pending,**name_context):
                self.window._flash_status('That playlist name is already used', 10)
                return False
            claimed.add(normalized)
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
        if request['operation'] in CREATE_SMART_OPERATIONS:
            from soundbox.rekordbox_playlist_store import (smart_creation_parent_reason,
                smart_append_creation_parent_reason,smart_normalized_creation_parent_reason)
            check={'create_smart':smart_creation_parent_reason,'create_smart_append':smart_append_creation_parent_reason,
                   'create_smart_normalized':smart_normalized_creation_parent_reason}[request['operation']]
            try:constructor=self.creation_catalog(presentation.source_playlist_catalog,request['parent'])
            except (ValueError,KeyError,TypeError) as exc:self.window._flash_status(str(exc),10);return False
            reason=check(constructor,request['parent'])
            if request['operation']!='create_smart_normalized':
                reason=reason or smart_creation_parent_reason(constructor,'root')
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
            _intent_xml_clocks(pending['request'],clocks)
        _intent_xml_clocks(request,clocks,advance=True)
        try:validate_request(request)
        except WriteProofError as exc:
            self.window._flash_status(str(exc),10);return False
        w=self.window;missing=object();old=w.settings.get(QUEUE,missing)
        job=dict(source=str(w.lib.source),library_id=w.lib.source_library_id,request=request,error='',retry=False)
        if tag_dependencies:job['tag_creation_dependencies']=tag_dependencies
        if placement_proof is not None:job['creation_placement']=placement_proof
        if any(command['operation']=='remove' for command in command_requests(request)):
            try:job['removal_overlay']=_capture_removal_overlay(request,getattr(w,'removals',{}),w.lib,selected_tracks)
            except (ValueError,KeyError,TypeError) as exc:w._flash_status(str(exc),10);return False
        if callable(getattr(w,'_commit_tag_state',None)):
            if not self.admission.stage(job,paired_settings=paired_settings):return False
            w._native_pending_changed(rebuild=True)
            self.status('Creating smart playlist…' if request['operation'] in CREATE_SMART_OPERATIONS else 'Updating playlist…')
            self.admission.pump();self.schedule()
            LOG.info('Playlist intention staged: operation=%s feedback_ms=%.2f',
                     request['operation'],(time.monotonic()-started)*1000)
            return True
        paired_before={key:(key in w.settings,w.settings.get(key)) for key in paired_settings or {}}
        w.settings.update(deepcopy(paired_settings or {}))
        w.settings[QUEUE]=[*(old if old is not missing else []),job]
        if not w._save_state():
            if old is missing:w.settings.pop(QUEUE,None)
            else:w.settings[QUEUE]=old
            for key,(present,value) in paired_before.items():
                if present:w.settings[key]=value
                else:w.settings.pop(key,None)
            return False
        admitted=time.monotonic()
        w._native_pending_changed(rebuild=True)
        LOG.info('Playlist intent admitted: operation=%s persist_ms=%.2f feedback_ms=%.2f',
                 request['operation'],(admitted-started)*1000,(time.monotonic()-started)*1000)
        self.status('Updating playlist…');self.schedule();return True

    def schedule(self):
        if not self.closing:self._pump_wakeup.schedule()

    def _checkpoint(self,before,after,*,replacements=None,current=None,message='Saving playlist intention…',committed=None):
        """Keep queue preparation and its paired retention on the canonical lane."""
        staged=deepcopy(self.admission.staged)
        def saved():
            self.not_before=0
            if committed is not None:committed()
            else:
                self.window._native_pending_changed(rebuild=True)
                self.status('Updating playlist…' if self.pending else '')
                self.schedule()
        def failed(error):
            self.status('Playlist confirmation needs retry — '+str(error))
            self.window._flash_status('Playlist intention NOT confirmed — retry or discard: '+str(error),15)
        accepted=self.ack.start(before,after,saved,failed,replacements=replacements,current=current,
            staged_after=staged,expected_staged=staged)
        if accepted and self.ack.pending and not self.ack.error:self.status(message)
        return accepted

    def _scheduled_pump(self):
        if self.closing:return
        error=self.queue_error()
        if error:self.status(error);return
        if self.pending:
            if self.pending[0].get('error'):self.status('Rekordbox playlist edit needs attention — '+self.pending[0]['error'])
            elif not self.tags.playlist_update_status:self.status('Playlist edits pending')
            self.pump()

    def _creation_successor(self,job,catalog):
        """Materialize placement only from the verified created native row."""
        proof=job.get('creation_placement')
        if proof is None:return None
        request=job['request']
        creation_placement.validate(proof,request,job['source'],job['library_id'])
        if not creation_placement.anchor_matches(creation_placement.anchor_from(proof),catalog,
                job['source'],job['library_id']):
            raise WriteProofError('Created playlist is saved; its original placement anchor changed. Review and retry the pending edit')
        targets=[row for row in catalog['nodes'] if row['ID']==request['playlist_id']]
        kind=4 if request['operation'] in CREATE_SMART_OPERATIONS else int(request['folder'])
        if (len(targets)!=1 or (targets[0].get('UUID'),targets[0].get('Name'),targets[0].get('Attribute'),
                targets[0].get('ParentID'),targets[0].get('rb_local_deleted'))!=(
                request['playlist_uuid'],request['name'],kind,proof['parent'],0)):
            raise WriteProofError('Created playlist identity changed before its saved placement')
        siblings=sorted((row for row in catalog['nodes'] if row['ParentID']==proof['parent']
                         and row['rb_local_deleted']==0),key=lambda row:row['Seq'])
        order=[row['ID'] for row in siblings]
        if order.index(request['playlist_id'])==order.index(proof['anchor_id'])+1:return None
        reason=self.node_move_reason()
        if reason:raise WriteProofError(reason)
        moved=request_for(catalog,'move',identity=request['playlist_id'],parent=proof['parent'],
                          anchor_id=proof['anchor_id'],after=True)
        return dict(source=job['source'],library_id=job['library_id'],request=moved,error='',retry=False)

    def pump(self):
        w=self.window
        if self.closing or not self.pending or self.admission.pending or self.ack.pending:return
        if self.queue_error():self.status(self.queue_error());return
        if self.pending[0].get('error'):return
        job=deepcopy(self.pending[0]);lib=w.lib
        if not lib or (job['source'],job['library_id'])!=(str(lib.source),lib.source_library_id):
            self.fail('Pending playlist edit belongs to a different rekordbox library',job);return
        waiting,error=self._tag_creation_wait(job)
        if error:self.fail(error,job);return
        if waiting:
            self.status('Waiting for the new My Tag to finish saving…');self.timer.start();return
        imports=getattr(w,'shared_import_controller',None)
        if imports and imports.source_priority:self.timer.start();return
        if (self.tags.source_job_owned or w._refresh_worker is not None or w._state_maintenance
                or device_blocks_library_work(w)):
            self.timer.start();return
        if self.reason():return
        if time.monotonic()<self.not_before:
            self.timer.start();return
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
            apply_kwargs={}
            if job['request']['operation'] in ('delete','delete_tree'):
                if not hasattr(store,'prepare_collection_pins'):
                    raise WriteProofError('Native playlist deletion needs exact Collection retention capture')
                if 'collection_pins' not in job:
                    return dict(collection_pins=store.prepare_collection_pins(job['request']))
                apply_kwargs['collection_pins']=job['collection_pins']
            apply_with_transition=getattr(store,'apply_with_transition',None)
            if callable(apply_with_transition):
                catalog,transition=apply_with_transition(job['request'],**apply_kwargs)
            else:
                catalog=store.apply(job['request'],**apply_kwargs)
                transition=((job['request']['revision'],catalog['revision'])
                            if job['request']['revision']==catalog['revision'] else None)
                proof=getattr(store,'committed_transition',None)
                if transition is None and proof is not None:transition=proof(job['request'])
            # A replay can return a fresh catalog containing independent native
            # edits. Its operation receipt alone cannot rebase our queued tail.
            # Capture the exact committed edge on this worker before GUI ACK.
            result = dict(catalog=catalog, transition=transition)
            if 'removal_overlay' in job:
                wanted={row['playlist_id'] for row in job['removal_overlay']['entries']};targets={}
                for node in catalog['nodes']:
                    if node['ID'] in wanted:
                        targets[node['ID']]=(node['UUID'] if node['ID'] not in targets and node.get('rb_local_deleted')==0
                                             and node.get('Attribute')==0
                                             else None)
                result['removal_overlay_targets']=targets
            if job['request']['operation'] in CREATE_SMART_OPERATIONS:
                result['library'] = self.tags.readback_library(lib)
            return result
        def completed(worker):
            if worker.error:self.fail(worker.error, job);return
            if worker.result.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Playlist edit pending — quit rekordbox to save')
                if not self.closing:self.timer.start()
                return
            if (not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id'])
                    or not self.pending or self.pending[0]['request']!=job['request']
                    or self.pending[0].get('creation_placement')!=job.get('creation_placement')
                    or self.pending[0].get('tag_creation_dependencies')!=job.get('tag_creation_dependencies')):
                self.fail('Saved in the original rekordbox library; switch back to finish this edit', job);return
            if 'collection_pins' in worker.result:
                if callable(getattr(w,'_commit_tag_state',None)):
                    before=deepcopy(w.settings.get(QUEUE,[]));pins=worker.result['collection_pins']
                    prepared=dict(w.settings)
                    try:_remember_collection_pins(prepared,job['library_id'],pins)
                    except (ValueError,WriteProofError) as exc:self.fail(str(exc),job);return
                    queue=deepcopy(before);queue[0]['collection_pins']=pins
                    previous=deepcopy(w.settings.get('rekordbox_collection_tracks',{}))
                    self._checkpoint(before,queue,
                        replacements={('settings','rekordbox_collection_tracks'):prepared['rekordbox_collection_tracks']},
                        current=lambda:w.settings.get('rekordbox_collection_tracks',{})==previous,
                        message='Preserving Collection before playlist deletion…')
                    return
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
                if callable(getattr(w,'_commit_tag_state',None)):
                    self._checkpoint(previous,jobs);return
                w.settings[QUEUE]=jobs
                if not w._save_state():
                    w.settings[QUEUE]=previous;self.fail('Could not save the renewed playlist intent', job);return
                self.not_before=0;self.schedule();return
            before=w.settings.get(QUEUE,[]);tail=list(before[1:])
            new_catalog=worker.result['catalog']
            if not w.lib or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']:
                self.fail('Saved in the original rekordbox library; switch back to finish this edit', job);return
            smart_library = worker.result.get('library')
            if job['request']['operation'] in CREATE_SMART_OPERATIONS and not _verified_smart_readback(
                    smart_library, new_catalog, job['request'], job['source'], job['library_id']):
                self.fail('Smart playlist readback could not be verified; the intention is preserved', job);return
            # Rebase only this FIFO's own still-unstarted edits. External source
            # changes remain conflicts; a worker never silently retargets them.
            sparse=callable(getattr(w,'_commit_tag_state',None))
            staged_before=deepcopy(self.admission.staged) if sparse else None
            durable_tail_count=len(tail)
            if staged_before:tail.extend(deepcopy(staged_before))
            tail_clocks={}
            _intent_xml_clocks(job['request'],tail_clocks)
            causal = worker.result.get('transition') == (job['request']['revision'], new_catalog['revision'])
            if 'creation_placement' in job:
                try:
                    if not causal:raise WriteProofError('The created playlist needs an exact native acknowledgement before its saved placement; review and retry')
                    successor=self._creation_successor(job,new_catalog)
                    if successor is not None:
                        _intent_xml_clocks(successor['request'],tail_clocks,advance=True)
                        tail.insert(0,successor);durable_tail_count+=1
                except (ValueError,KeyError,TypeError,WriteProofError) as exc:
                    self.fail(str(exc),job);return
            for index,later in enumerate(tail):
                if (later['source']==job['source'] and later['library_id']==job['library_id']
                        and later['request']['revision']==job['request']['revision']):
                    later=deepcopy(later);tail[index]=later
                    if not causal:
                        later['error']='Native library changed outside this saved operation; review and retry this queued edit'
                        continue
                    if later['request']['operation'] in (*CREATE_SMART_OPERATIONS, 'delete_smart', 'delete_tree','move_smart','move','create_normalized','create_folder_normalized'):
                        from soundbox.rekordbox_playlist_store import (renew_smart_request,
                            renew_smart_delete_request,renew_tree_delete_request,renew_smart_move_request,
                            renew_normalized_create_request,renew_node_move_request)
                        try:
                            renew={**dict.fromkeys(CREATE_SMART_OPERATIONS,renew_smart_request),'delete_smart':renew_smart_delete_request,
                                   'delete_tree':renew_tree_delete_request,'move_smart':renew_smart_move_request,
                                   'move':renew_node_move_request,
                                   'create_normalized':renew_normalized_create_request,
                                   'create_folder_normalized':renew_normalized_create_request}[later['request']['operation']]
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
            if sparse:
                staged_after=tail[durable_tail_count:];tail=tail[:durable_tail_count]
                replacements={};captured_removals=getattr(w,'removals',None)
                captured_pins=deepcopy(w.settings.get('rekordbox_collection_tracks',{}))
                if smart_library is None:
                    # Compute paired canonical deltas without publishing a new
                    # catalog or removal mask before their durable ACK.
                    pins=_catalog_collection_pins(w.lib,new_catalog,job['request'],captured_pins)
                    if pins!=captured_pins:replacements[('settings','rekordbox_collection_tracks')]=pins
                    if 'removal_overlay' in job and captured_removals is not None:
                        removals=_ack_removal_overlay(captured_removals,job['removal_overlay'],
                            worker.result.get('removal_overlay_targets',{}),w.lib)
                        if removals!=captured_removals:replacements[('playlist_removals',)]=removals
                captured_removals=deepcopy(captured_removals)
                def current():
                    return (w.settings.get('rekordbox_collection_tracks',{})==captured_pins
                        and getattr(w,'removals',None)==captured_removals)
                def acknowledged():
                    self.not_before=0
                    if smart_library is None:w._native_apply_catalog(new_catalog,job['request'])
                    if not self.closing:
                        if smart_library is not None:w._publish_created_library(smart_library)
                        else:w._native_refresh_view(job['request'])
                    self.status('Updating playlist…' if self.pending else '')
                    finish=getattr(w,'_finish_edit_status',None)
                    if not self.pending and finish:finish(quiet=True)
                    self.schedule()
                def failed(error):
                    self.status('Playlist saved in rekordbox; local confirmation needs retry')
                    w._flash_status('Playlist saved in rekordbox; local confirmation failed — retry or discard the pending edit: '+str(error),15)
                accepted=self.ack.start(before,tail,acknowledged,failed,
                    replacements=replacements,current=current,staged_after=staged_after,expected_staged=staged_before)
                if not accepted:failed(RuntimeError('Another playlist acknowledgement still owns this save'))
                return
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
                    and (current.get('source'),current.get('library_id'),current.get('request'),current.get('creation_placement'),
                         current.get('tag_creation_dependencies'))
                    == (job['source'],job['library_id'],job['request'],job.get('creation_placement'),
                        job.get('tag_creation_dependencies'))),None)
        if owned is not None:
            if callable(getattr(w,'_commit_tag_state',None)):
                before=deepcopy(w.settings.get(QUEUE,[]))
                # Staged rows belong to admission; a source failure must never
                # smuggle them into the durable queue or duplicate them.
                if owned>=len(before):
                    self.admission._failed(RuntimeError(message));return
                jobs=deepcopy(before);jobs[owned]['error']=message
                def recorded():
                    if w.lib and (str(w.lib.source),w.lib.source_library_id)==(job['source'],job['library_id']):
                        w._native_pending_changed(rebuild=True);w._native_reconcile_source()
                    self.status('Rekordbox playlist edit needs attention — '+message)
                self._checkpoint(before,jobs,committed=recorded);return
            jobs[owned]={**jobs[owned],'error':message};w.settings[QUEUE]=jobs;w._save_state()
            if w.lib and (str(w.lib.source),w.lib.source_library_id)==(job['source'],job['library_id']):
                w._native_pending_changed(rebuild=True)
                w._native_reconcile_source()
            self.status('Rekordbox playlist edit needs attention — '+message)
        else:w._flash_status('Original playlist operation: '+message,12)

    def retry(self):
        if self.ack.pending:
            accepted=self.ack.retry()
            if accepted:self.status('Saving playlist confirmation…')
            return accepted
        if self.admission.pending:
            accepted=self.admission.retry()
            if accepted:self.status('Saving pending playlist intention…')
            return accepted
        if not self.pending:return
        if self.queue_error():self.window._flash_status(self.queue_error(),15);return
        w=self.window;previous=self.pending;jobs=list(previous)
        jobs[0]={**jobs[0],'error':'','retry':True}
        if callable(getattr(w,'_commit_tag_state',None)):
            return self._checkpoint(previous,jobs)
        w.settings[QUEUE]=jobs
        if w._save_state():
            w._native_pending_changed(rebuild=True)
            self.status('Updating playlist…');self.not_before=0;self.schedule()
        else:w.settings[QUEUE]=previous

    def discard(self):
        from .intent_review import capture_discard,discard_is_current
        review=capture_discard(self)
        if review is None:return False
        w=self.window
        if QMessageBox.question(w,'Discard pending playlist edits',
                f'Discard {len(review.jobs)} pending playlist edit(s)?\nEdits already saved in rekordbox remain saved.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return False
        if not discard_is_current(self,review):
            w._flash_status('A save started or the pending edits changed; review them and try again.',12)
            return False
        if self.ack.pending:return self.ack.discard()
        if self.admission.pending:return self.admission.discard()
        previous=self.pending
        if callable(getattr(w,'_commit_tag_state',None)):
            def discarded():
                self.timer.stop();self.status('');w._native_pending_changed(rebuild=True)
                w._native_reconcile_source()
            return self._checkpoint(previous,[],committed=discarded)
        w.settings[QUEUE]=[]
        if not w._save_state():
            w.settings[QUEUE]=previous
            return False
        else:
            self.timer.stop();self.status('');w._native_pending_changed(rebuild=True)
            w._native_reconcile_source()
            return True

    def stop(self):
        self.closing=True;self.timer.stop();self._pump_wakeup.stop()
        self.ack.stop();self.admission.stop()


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
        projection_jobs,projection_tags=controller.presentation_tag_inputs(pending)
        project_input=replace(self.lib,source_tags=projection_tags) if projection_tags!=self.lib.source_tags else self.lib
        if pending and self.lib.source_playlist_catalog and not (imports and imports.pending):
            lib=self.lib;catalog=lib.source_playlist_catalog
            # Compare native scalar rows and copied durable intents by value,
            # including in-place UUID/error/clock changes. Object identity alone
            # cannot establish a valid presentation after source reconciliation.
            identities=tuple((key,t.track_id,t.native_id,t.source_uuid) for key,t in lib.tracks_by_id.items())
            smart={r['ID'] for r in catalog.get('nodes',[]) if r.get('Attribute')==4}
            intelligent=tuple((p.pid,tuple(p.keys)) for p in lib.playlists if p.pid in smart)
            pending_smart=any(job['request']['operation'] in CREATE_SMART_OPERATIONS for job in pending)
            tag_inputs=((tuple((key,tuple(track.source_tag_ids or ()),tuple(sorted(track.source_tag_times.items())))
                for key,track in lib.tracks_by_id.items()),
                tuple((tag.tag_id,tag.source_uuid,tag.name,tag.category,tag.category_id,tag.category_uuid)
                      for tag in projection_tags)) if pending_smart else None)
            policy=tuple((job['request']['operation_id'],job.get('error','')) for job in projection_jobs)
            inputs=(str(lib.source),lib.source_library_id,identities,intelligent,tag_inputs,policy)
            cached=getattr(self,'_native_presentation_cache',None)
            if not (cached and cached[0]==inputs and cached[1]==catalog and cached[2]==pending):
                projected=project(project_input,projection_jobs)
                cached=(inputs,copy_catalog(catalog),deepcopy(pending),projected.playlists,projected.source_playlist_catalog)
                self._native_presentation_cache=cached
            # Current filters, hidden IDs, artist registry, tracks, source tags
            # and other library metadata always come from the current Library.
            library=replace(lib,playlists=cached[3],source_playlist_catalog=cached[4])
        else:
            self._native_presentation_cache=None
            projected=project(project_input,projection_jobs)
            library=(replace(self.lib,playlists=projected.playlists,source_playlist_catalog=projected.source_playlist_catalog)
                     if project_input is not self.lib else projected)
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
                for command in command_requests(job['request']) if command['operation'] in CREATE_ORDINARY_OPERATIONS}
        presentation=self._native_presentation() if presentation is None else presentation
        visible={r['ID'] for r in presentation.source_playlist_catalog.get('nodes',[]) if r['rb_local_deleted']==0}
        return known-visible

    @preserve_inline_drafts
    def _native_reload_pending_playlist(self,display_path):
        """Playlist-only feedback reuses art already prepared for these rows."""
        if self.stack.currentIndex()==1:return
        from .tag_selection_view import rows as selected_rows
        rows=selected_rows(self,self._playlist_rows(display_path))
        prepared={};uncertain=set()
        for row in self.model._all_rows:
            identity=row.get('id');kind=row.get('art')
            if not isinstance(kind,str):continue
            if identity in prepared and prepared[identity]!=kind:uncertain.add(identity)
            prepared[identity]=kind
        if any(row.get('id') not in prepared or row.get('id') in uncertain for row in rows):
            self._reload_current_view();return
        for row in rows:row['art']=prepared[row['id']]
        seat=self._browser_seat();previous=self.model._rows
        self._set_refreshed_rows(rows)
        if self.model._rows is not previous:self._restore_browser_seat(seat)

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
            if self.stack.currentIndex() == 1:
                self._view_key='sb:Collection'
            else:
                self._select_only(self.sb_tree)
                item=self.sb_tree.topLevelItem(0)
                if item and item.data(0,COLLECTION_ROLE):
                    self.sb_tree.blockSignals(True)
                    try:self.sb_tree.setCurrentItem(item)
                    finally:self.sb_tree.blockSignals(False)
                self._show_collection()
                self._restore_view('sb:Collection')
        if rebuild:
            updated=None
            if current.startswith('rb:') and active:
                updated=next((p for p in presentation.playlists if p.pid==active),None)
                if updated:self._view_key='rb:'+updated.display_path
            self._populate_tree(engine.summary(self.lib),presentation=presentation)
            if current.startswith('rb:') and active not in removed:
                if updated is not None:self._native_reload_pending_playlist(updated.display_path)
                else:self._reload_current_view()
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

    def _native_request(self, operation, *, creation_position=None, **kwargs):
        reason=self.playlist_controller.reason()
        if reason:self._flash_status(reason,10);return False
        # Only target identity/presentation comes from the projected view. The
        # captured revision remains the verified source baseline; FIFO rebase
        # renews it after preceding accepted operations commit.
        try:
            catalog=self._native_presentation().source_playlist_catalog
            if operation=='create':
                from soundbox.rekordbox_playlist_store import normalized_creation_required
                if (normalized_creation_required(catalog,'root')
                        or normalized_creation_required(catalog,kwargs.get('parent','root'))):
                    operation='create_folder_normalized' if kwargs.get('folder') else 'create_normalized'
            request=request_for(catalog,operation,**kwargs)
        except (ValueError,KeyError,WriteProofError) as exc:self._flash_status(str(exc),10);return False
        enqueue_kwargs={'placement':creation_position} if creation_position is not None else {}
        if operation=='remove':enqueue_kwargs['selected_tracks']=kwargs.get('tracks',())
        accepted=self.playlist_controller.enqueue(request,**enqueue_kwargs)
        if accepted and operation in CREATE_ORDINARY_OPERATIONS and hasattr(self,'tree'):
            item=self._find_tree_item('',request['playlist_id'])
            if item:
                if item.data(0,PATH_ROLE):self.tree.setCurrentItem(item)
                else:item.setExpanded(True)
        return accepted

    def _native_creation_anchor(self,identity):
        row=self._native_node(identity)
        if not row or row['rb_local_deleted']!=0 or row['Attribute'] not in (0,1,4):return None
        parent=self._native_parent_scope(row['ParentID'])
        if parent is None:return None
        return dict(source=parent[0],library_id=parent[1],anchor_id=row['ID'],anchor_uuid=row['UUID'],
                    anchor_attribute=row['Attribute'],parent=row['ParentID'],parent_uuid=parent[3])

    def _native_creation_anchor_current(self,anchor,parent):
        if anchor is None:return True
        library=self._native_presentation()
        return bool(library and parent==anchor.get('parent') and creation_placement.anchor_matches(
            anchor,library.source_playlist_catalog,library.source,library.source_library_id))

    def _native_new(self, parent='root', *, folder=False, expected_parent=None, placement=None):
        reason=self.playlist_controller.reason()
        if reason:self._flash_status(reason,10);return
        scope=self._native_parent_scope(parent)
        if scope is None or expected_parent is not None and scope!=expected_parent:
            self._flash_status('The destination folder changed; open its menu again',10);return
        if not self._native_creation_anchor_current(placement,parent):
            self._flash_status('The playlist position changed; open its menu again',10);return
        if placement is not None:
            reason=self.playlist_controller.node_move_reason()
            if reason:self._flash_status(reason,10);return
        label='Folder' if folder else 'Playlist'
        name,ok=QInputDialog.getText(self,'New rekordbox '+label,'Name:')
        name=name.strip()
        if not ok or not name:return
        if self._native_parent_scope(parent)!=scope or not self._native_creation_anchor_current(placement,parent):
            self._flash_status('The destination changed while naming; choose the folder again',10);return
        if any(r['ParentID']==parent and r['Name']==name and r['rb_local_deleted']==0
               for r in self._native_presentation().source_playlist_catalog['nodes']):
            self._flash_status('A playlist or folder with that name already exists here',8);return
        position={'creation_position':placement} if placement is not None else {}
        self._native_request('create',name=name,parent=parent,folder=folder,**position)

    def _native_reorder(self,identity,anchor_id,after):
        reason=self.playlist_controller.reorder_reason()
        if reason:self._flash_status(reason,8);return False
        item=self._find_tree_item('',identity)
        if item is None or item.data(0,PINNED_TREE_ROLE):return False
        target=self._native_node(identity);anchor=self._native_node(anchor_id)
        if (not target or not anchor or target['ParentID']!=anchor['ParentID']
                or not self._native_painted_binding(identity,target)
                or not self._native_painted_binding(anchor_id,anchor)):return False
        siblings=sorted((r for r in self._native_presentation().source_playlist_catalog['nodes']
                         if r['ParentID']==target['ParentID'] and r['rb_local_deleted']==0),key=lambda r:r['Seq'])
        original=[r['ID'] for r in siblings];updated=[value for value in original if value!=identity]
        if anchor_id not in updated:return False
        updated.insert(updated.index(anchor_id)+int(after),identity)
        if original==updated:return True
        if [row['Seq'] for row in siblings]!=list(range(1,len(siblings)+1)):
            reason=self.playlist_controller.node_move_reason()
            if reason:self._flash_status(reason,8);return False
            return self._native_request('move',identity=identity,parent=target['ParentID'],
                                        anchor_id=anchor_id,after=after)
        start,end=sorted((original.index(identity),original.index(anchor_id)))
        operation='reorder_smart' if any(row['Attribute']==4 for row in siblings[start:end+1]) else 'reorder'
        return self._native_request(operation,identity=identity,anchor_id=anchor_id,after=after)

    def _native_painted_binding(self,identity,row):
        item=self._find_tree_item('',identity)
        if item is None or item.data(0,PINNED_TREE_ROLE):return False
        captured=item.data(0,NATIVE_BINDING_ROLE)
        if captured is None:return True
        try:binding=native_playlist_binding(captured)
        except (ValueError,TypeError):return False
        return (binding['source'],binding['library_id'],binding['id'],binding['uuid'])==(
            str(self.lib.source),self.lib.source_library_id,row['ID'],row['UUID'])

    def _native_move(self,identity,parent):
        return self._native_place(identity,parent)

    def _native_place(self,identity,parent,anchor_id='',after=False):
        target=self._native_node(identity)
        if (not target or target['Attribute'] not in (0,1,4) or target['rb_local_deleted']!=0
                or not self._native_painted_binding(identity,target)
                or self._native_parent_scope(parent) is None):return False
        destination=self._native_node(parent) if parent!='root' else None
        if destination and not self._native_painted_binding(parent,destination):return False
        anchor=self._native_node(anchor_id) if anchor_id else None
        if anchor_id and (not anchor or anchor['ParentID']!=parent
                          or not self._native_painted_binding(anchor_id,anchor)):return False
        if target['ParentID']==parent:
            if anchor_id:return self._native_reorder(identity,anchor_id,after)
            return True
        # Prefer the complete placement domain once independently accepted.
        # Legacy smart append remains available under its original authority.
        node_reason=self.playlist_controller.node_move_reason()
        operation='move_smart' if target['Attribute']==4 and not anchor_id and node_reason else 'move'
        reason=(self.playlist_controller.smart_move_reason() if operation=='move_smart'
                else node_reason)
        if reason:self._flash_status(reason,8);return False
        kwargs=dict(identity=identity,parent=parent)
        if operation=='move':kwargs.update(anchor_id=anchor_id or None,after=after)
        return self._native_request(operation,**kwargs)

    def _native_rename(self, identity, *, expected=None):
        row=self._native_node(identity)
        if row is None:return
        scope=self._native_node_scope(row)
        if expected is not None and scope!=expected:
            self._flash_status('The playlist changed; open its menu again',10);return
        if row['Attribute']==4:
            reason=self.playlist_controller.smart_edit_reason('rename_smart')
            if reason:self._flash_status(reason,10);return
        elif row['Attribute'] not in (0,1):return
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
            self._native_request('rename_smart' if row['Attribute']==4 else 'rename',identity=identity,name=name)

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
        row=self._native_node(identity);parent=row['ParentID'] if row else 'root'
        scope=self._native_node_scope(row)
        parent_scope=self._native_parent_scope(parent)
        placement=self._native_creation_anchor(identity)
        creation_available=not identity or placement is not None
        painted=item.data(0,NATIVE_BINDING_ROLE) if item is not None else None
        if placement is not None and painted is not None:
            try:
                binding=native_playlist_binding(painted)
                creation_available=(binding['source'],binding['library_id'],binding['id'],binding['uuid'])==(
                    placement['source'],placement['library_id'],placement['anchor_id'],placement['anchor_uuid'])
            except (ValueError,TypeError):creation_available=False
        reason=self.playlist_controller.reason()
        for label,callback,available in (
            ('New Playlist',lambda:self._native_new(parent,expected_parent=parent_scope,placement=placement),creation_available),
            ('New Smart Playlist',lambda:self._new_smart_playlist(parent,placement=placement),creation_available),
            ('New Folder',lambda:self._native_new(parent,folder=True,expected_parent=parent_scope,placement=placement),creation_available),
            ('Rename',lambda:self._native_rename(identity,expected=scope),bool(row and row['Attribute'] in (0,1,4))),
            ('Delete',lambda:self._native_delete(identity,expected=scope),bool(row and row['Attribute'] in (0,1,4)))):
            action_reason = (self.playlist_controller.smart_creation_reason() if label == 'New Smart Playlist'
                             else self.playlist_controller.smart_edit_reason('rename_smart')
                             if label=='Rename' and row and row['Attribute']==4
                             else self.playlist_controller.smart_delete_reason(identity)
                             if label == 'Delete' and row and row['Attribute'] == 4 else reason)
            if label in ('New Playlist','New Smart Playlist','New Folder') and placement is not None and not action_reason:
                action_reason=self.playlist_controller.node_move_reason()
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
            from .intent_review import capture_discard
            discard.setEnabled(capture_discard(self.playlist_controller) is not None)
        definitions = getattr(self, 'tag_definition_controller', None)
        if definitions and (definitions.pending or getattr(definitions,'publication_retry_pending',False)):
            menu.addSeparator()
            from .intent_review import capture_discard
            recoverable=bool(definitions.pending) and capture_discard(definitions) is not None
            retry = menu.addAction('Retry tag changes', definitions.retry)
            retry.setEnabled(recoverable or getattr(definitions,'publication_retry_ready',False)
                             or getattr(definitions,'acknowledgement_retry_ready',False))
            if definitions.pending:
                discard = menu.addAction('Discard pending tag changes', definitions.discard)
                discard.setEnabled(recoverable)
        tags=self.tag_controller
        physical=bool(getattr(tags,'busy',False) or getattr(tags,'checking',False)
                      or getattr(getattr(tags,'owner',None),'pending',0))
        metadata=getattr(self,'metadata_controller',None)
        if metadata and (metadata.pending or getattr(metadata,'migration_pending',False)
                         or getattr(metadata,'retirement_pending',False)):
            menu.addSeparator()
            summary = getattr(metadata, 'background_summary', '')
            if summary:
                progress = menu.addAction(summary)
                progress.setEnabled(False)
                progress.setToolTip(getattr(tags, 'metadata_update_status', ''))
            paused=metadata.background_paused
            pause_scope=(str(self.lib.source),self.lib.source_kind,self.lib.source_library_id)
            pause=menu.addAction('Resume background song updates' if paused else 'Pause background song updates',
                lambda:self._set_metadata_background_paused(not paused,pause_scope))
            pause.setEnabled(not getattr(self,'_state_publication',False))
            retry=menu.addAction('Retry metadata saves',metadata.retry);retry.setEnabled(not physical)
            error=metadata.pending[0].get('error','') if metadata.pending else metadata.queue_error()
            if error:retry.setToolTip(error)
        lane=getattr(tags,'tag_state_lane',None)
        unsaved=bool(getattr(lane,'failed',()))
        if getattr(tags,'pending',[]) or unsaved:
            menu.addSeparator()
            retry=menu.addAction('Retry My Tag assignments',tags.retry);retry.setEnabled(not physical)
            error=tags.pending[0].get('error','') if tags.pending else getattr(lane,'error','')
            if error:retry.setToolTip(error)
            if unsaved:
                discard=menu.addAction('Discard unsaved My Tag clicks',tags.discard)
                discard.setEnabled(not physical)
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
        self.settings['rekordbox_collection_tracks']=_catalog_collection_pins(lib,catalog,request,
            self.settings.get('rekordbox_collection_tracks',{}))
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

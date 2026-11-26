"""Durable tag-definition intents on the existing joined source-worker lane."""
from copy import deepcopy
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox import rekordbox_tag_definitions as definitions
from soundbox import rekordbox_tag_order as tag_order
from soundbox import rekordbox_tag_rename as tag_rename
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed
from .tag_controller import device_blocks_library_work
from .source_intent_admission import SourceIntentAdmission, SourceQueueAcknowledgement

QUEUE = 'pending_tag_definitions'


class TagDefinitionController(QObject):
    def __init__(self, window, *, store_factory=None, deletion_store_factory=None, order_store_factory=None,
                 rename_store_factory=None,loader=None):
        super().__init__(window)
        self.window = window
        self.tags = window.tag_controller
        self.factory = store_factory
        self.deletion_factory = deletion_store_factory
        self.order_factory = order_store_factory
        self.rename_factory = rename_store_factory
        self.loader = loader
        self.closing = False
        # Creation and ordering share the same ACK-to-publication handoff;
        # a durable queue removal cannot expose an older catalog to new input.
        self._creation_publication = None
        self._creation_publication_error = ''
        self.not_before = 0.0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule)
        self.tags.changed.connect(self.schedule)
        self.admission = SourceIntentAdmission(self, QUEUE, self._admission_changed,
            current_owner=lambda:getattr(window,'tag_definition_controller',self) is self)
        self.ack = SourceQueueAcknowledgement(self, QUEUE)

    @property
    def pending(self):
        queue = self.window.settings.get(QUEUE, [])
        queue = queue if isinstance(queue, list) else []
        return [*queue, *self.admission.rows] if self.admission.staged else queue

    @property
    def admission_pending(self):return self.admission.pending or self.ack.pending

    @property
    def admission_error(self):return self.ack.error or self.admission.error

    def flush_admission(self):return self.ack.flush() and self.admission.flush()

    def _admission_changed(self, outcome):
        if outcome == 'failed':
            self.status('Tag change NOT saved — ' + self.admission.error)
            self.window._flash_status('Tag change NOT saved — retry or discard the pending intention', 15)
        elif outcome == 'discarded':
            self.status('');self.window._native_reconcile_source()
        else:
            if self.pending:self.status(self._label(self.pending[0]['request']))
            self.schedule()

    def queue_error(self):
        queue = self.pending if isinstance(self.window.settings.get(QUEUE, []), list) else None
        try:
            if not isinstance(queue, list) or len(queue) > 256:
                raise ValueError('Invalid queue')
            seen, tag_ids, tag_uuids = set(), set(), set()
            ordering=bool(queue) and all(self._ordering(job.get('request'))
                for job in queue if isinstance(job,dict))
            chains={};scope=None;identities={};uuids={}
            for job in queue:
                if (not isinstance(job, dict) or set(job) != {'source', 'library_id', 'request', 'error'}
                        or not isinstance(job['source'], str) or not Path(job['source']).is_absolute()
                        or not isinstance(job['library_id'], str) or not job['library_id']
                        or not isinstance(job['error'], str)):
                    raise ValueError('Invalid intent')
                self._validate(job['request'])
                request=job['request']
                if (request['library_id'] != job['library_id'] or request['operation_id'] in seen
                        or not ordering and (request['tag_id'] in tag_ids or request['tag_uuid'] in tag_uuids)):
                    raise ValueError('Invalid intent identity')
                seen.add(request['operation_id'])
                tag_ids.add(request['tag_id'])
                tag_uuids.add(request['tag_uuid'])
                if ordering:
                    current_scope=(job['source'],job['library_id'])
                    if scope is not None and scope!=current_scope:raise ValueError('Different tag order sources')
                    scope=current_scope
                    category=request['category_id']
                    before=(request['category_uuid'],tuple((row['id'],row['uuid'],row['name'])
                        for row in request['before']))
                    if category in chains and chains[category]!=before:
                        raise ValueError('Broken tag order chain')
                    for row in request['before']:
                        identity=(category,request['category_uuid'],row['uuid'],row['name'])
                        uuid=(category,request['category_uuid'],row['id'],row['name'])
                        if (row['id'] in identities and identities[row['id']]!=identity
                                or row['uuid'] in uuids and uuids[row['uuid']]!=uuid):
                            raise ValueError('A tag identity belongs to a different category')
                        identities[row['id']]=identity;uuids[row['uuid']]=uuid
                    by_id={row['id']:row for row in request['before']}
                    chains[category]=(request['category_uuid'],tuple(
                        (identity,by_id[identity]['uuid'],by_id[identity]['name']) for identity in request['order']))
        except (KeyError, TypeError, ValueError, WriteProofError):
            return 'Saved tag changes need attention; intentions are preserved'
        return ''

    def reason(self):
        w = self.window
        if self.publication_retry_pending:return self._creation_publication_error
        if self.queue_error():
            return self.queue_error()
        if any(self._ordering(job['request']) for job in self.pending):
            return 'Finish pending tag order changes before changing tag definitions'
        if self.ack.pending:return 'Finishing the saved tag change acknowledgement'
        if self.admission.error:return 'The pending tag intention needs retry or discard'
        if self.closing or w._shutting_down or w._state_maintenance:
            return 'Library operation in progress'
        lib = w.lib
        if (not lib or lib.source_kind != 'rekordbox-db' or not self.tags.verified
                or not lib.source_library_id or not lib.source_tag_categories):
            return 'Refresh the rekordbox library before creating tags'
        if not self.factory and definitions.load_authority(
                self.tags.data_root / 'rekordbox-tag-definition-authority.json',
                Path(lib.source), lib.source_library_id) is None:
            return 'Creating tags is not enabled for this library yet'
        return ''

    @staticmethod
    def _deleting(request):
        return isinstance(request, dict) and request.get('action') == 'delete'

    @staticmethod
    def _ordering(request):
        return isinstance(request,dict) and request.get('action')=='reorder'

    @staticmethod
    def _renaming(request):
        return isinstance(request,dict) and request.get('action')=='rename'

    @classmethod
    def _label(cls,request):
        return ('Renaming tag…' if cls._renaming(request) else 'Reordering tags…' if cls._ordering(request)
                else 'Deleting tag…' if cls._deleting(request) else 'Creating tag…')

    @staticmethod
    def _desired_name(request):return request.get('new_name',request['name'])

    def assignment_reason(self,tag_id=None,tag_uuid=None):
        """Old-name assignment captures never cross a pending rename."""
        if tag_id is None:return ''
        creation=self.pending_creation(tag_id,tag_uuid)
        if creation and creation['error']:
            return 'New tag creation needs attention — '+creation['error']
        lib=self.window.lib
        return ('Finish or discard the pending tag rename before assigning My Tags'
                if lib and any(self._renaming(job.get('request'))
                    and (job.get('source'),job.get('library_id'))==(str(lib.source),lib.source_library_id)
                    and job['request']['tag_id']==tag_id
                    and (tag_uuid is None or job['request']['tag_uuid']==tag_uuid)
                    for job in self.pending if isinstance(job,dict)) else '')

    @staticmethod
    def _creating(request):
        return isinstance(request,dict) and 'action' not in request

    def _creation_jobs(self):
        """Pending identities only; verified Library and export inputs stay exact."""
        lib=self.window.lib
        if not lib or lib.source_kind!='rekordbox-db' or self.queue_error():return []
        jobs=list(self.window.settings.get(QUEUE,[]))
        if (self.admission.staged and self.admission._scope==self.admission._context()
                and self.admission.current_owner()):jobs.extend(self.admission.rows)
        bridge=self._publication_handoff()
        if bridge is not None:
            job,scope=bridge
            if scope==self.admission._context() and self.admission.current_owner():
                if not any(row['request']['operation_id']==job['request']['operation_id'] for row in jobs):
                    jobs.insert(0,job)
        categories={(row.category_id,row.source_uuid):row for row in lib.source_tag_categories}
        return [job for job in jobs if self._creating(job['request'])
                and (job['source'],job['library_id'])==(str(lib.source),lib.source_library_id)
                and (job['request']['category_id'],job['request']['category_uuid']) in categories]

    def pending_creation(self,tag_id,tag_uuid=None):
        matches=[job for job in self._creation_jobs() if job['request']['tag_id']==tag_id
                 and (tag_uuid is None or job['request']['tag_uuid']==tag_uuid)]
        if len(matches)!=1:return None
        job=deepcopy(matches[0])
        preceding=''
        pending=self.pending
        if any(queued['request']['operation_id']==job['request']['operation_id'] for queued in pending):
            for queued in pending:
                if queued.get('error'):
                    preceding=queued['error'];break
                if queued['request']['operation_id']==job['request']['operation_id']:break
        job['error']=job['error'] or self.admission.error or self.ack.error or preceding or self._creation_publication_error
        return job

    @property
    def publication_retry_pending(self):
        return bool(self._creation_publication is not None and self._creation_publication_error
            and self._creation_publication[1]==self.admission._context() and self.admission.current_owner())

    def _publication_matches(self,library,request):
        if self._ordering(request):return self._ordered_tags_match(library.source_tags,request,after=True)
        return any((tag.tag_id,tag.source_uuid,tag.name,tag.category_id,tag.category_uuid)==
            tuple(request[key] for key in ('tag_id','tag_uuid','name','category_id','category_uuid'))
            for tag in library.source_tags)

    def _publication_handoff(self):
        """Bridge the durable queue ACK to complete verified Library publication."""
        handoff=self._creation_publication
        if (handoff is None or handoff[1]!=self.admission._context()
                or not self.admission.current_owner()):return None
        job,_scope=handoff
        libraries=(self.window.lib,getattr(self.tags,'lib',self.window.lib))
        if all(library is not None and self._publication_matches(library,job['request'])
               for library in libraries):
            self._creation_publication=None;self._creation_publication_error=''
            return None
        return handoff

    @property
    def publication_retry_ready(self):
        w=self.window
        return bool(self.publication_retry_pending and not self.admission_pending
            and not self.closing and not w._shutting_down and not w._state_maintenance
            and w._refresh_worker is None and not self.tags.source_job_owned and not self.tags.prep_priority
            and not device_blocks_library_work(w))

    @property
    def acknowledgement_retry_ready(self):
        """A failed exact ACK can retry while its unsaved dependents wait."""
        w=self.window
        return bool(self.ack.pending and self.ack.error and not self.ack.active and self.ack._current()
            and not self.closing and not w._shutting_down and not w._state_maintenance
            and not self.tags.busy and not getattr(self.tags,'checking',False)
            and not getattr(self.tags,'state_commit_owned',False)
            and w._refresh_worker is None and not getattr(w,'_state_publication',False)
            and getattr(self.tags,'_source_ack_tokens',{self.ack.token})=={self.ack.token}
            and not device_blocks_library_work(w))

    def _retry_publication(self):
        if not self.publication_retry_ready:return False
        handoff=self._creation_publication;job,scope=handoff;lib=self.window.lib
        suffix=deepcopy(self.window.settings.get(QUEUE,[]));token=object()
        def work():
            # This is a fresh read, never another definition apply or receipt
            # replay. Native source writers retain their original protocols.
            return self.loader(Path(lib.source)) if self.loader is not None else self.tags.readback_library(lib)
        def completed(worker):
            try:finish(worker)
            finally:self.tags.release_source_ack(token)
        def finish(worker):
            if self._creation_publication is not handoff or scope!=self.admission._context():return
            error=worker.error
            if self.window.settings.get(QUEUE,[])!=suffix:
                error='Pending tag changes changed during publication retry; review and retry'
            if not error:
                try:
                    library=worker.result;request=job['request']
                    if (library.source_kind!='rekordbox-db'
                            or (str(library.source),library.source_library_id)!=(job['source'],job['library_id'])
                            or not self._publication_matches(library,request)):
                        raise RuntimeError('The saved tag definition changed before publication retry')
                    self.window._publish_created_library(library)
                    self._creation_jobs()
                    if self._creation_publication is not None:
                        raise RuntimeError('The saved tag library publication is incomplete')
                except Exception as exc:error=str(exc)
            if error:
                self._creation_publication_error='Tag saved; library publication needs retry — '+error[:3000]
                self.status(self._creation_publication_error)
            else:
                self._creation_publication_error='';self.status('')
            self.tags.changed.emit();self.tags.idle.emit()
        started=self.tags._run_source_job('tag-definitions',work,completed)
        if started:self.tags.reserve_source_ack(token)
        return started

    def _record_publication_blocker(self):
        """Retain a blocked successor honestly without rewriting its request."""
        if not self.publication_retry_pending or self.closing or self.window._shutting_down:return
        if self.ack.pending:
            if not self.ack.error:QTimer.singleShot(50,self,self._record_publication_blocker)
            return
        queue=self.window.settings.get(QUEUE,[])
        if queue and not queue[0].get('error'):
            self.fail('Earlier tag library publication needs retry — '+self._creation_publication_error,
                      deepcopy(queue[0]))

    def creation_dependency(self,tag_id,tag_uuid=None):
        job=self.pending_creation(tag_id,tag_uuid)
        if job is None:return None
        request=job['request']
        return dict(source=job['source'],library_id=job['library_id'],
            **{key:request[key] for key in ('operation_id','tag_id','tag_uuid','name','category_id','category_uuid')})

    def creation_durable(self,dependency):
        if self.creation_dependency(dependency['tag_id'],dependency['tag_uuid'])!=dependency:return False
        operation=dependency['operation_id']
        return (any(job['request']['operation_id']==operation for job in self.window.settings.get(QUEUE,[]))
                or self._creation_publication is not None
                and self._creation_publication[0]['request']['operation_id']==operation)

    def pending_creation_reason(self,tag_id,tag_uuid=None):
        job=self.pending_creation(tag_id,tag_uuid)
        return ('New tag creation needs attention — '+job['error'] if job and job['error'] else
                'Finish creating this tag before changing its definition' if job else '')

    def available_tags(self,tags=None):
        from soundbox.library import LibraryTag
        lib=self.window.lib
        result=self.native_order(lib.source_tags if tags is None and lib else tags or [])
        if lib is None:return result
        categories={(row.category_id,row.source_uuid):row for row in lib.source_tag_categories}
        for job in self._creation_jobs():
            request=job['request']
            if any(tag.tag_id==request['tag_id'] or tag.source_uuid==request['tag_uuid'] for tag in result):continue
            category=categories[(request['category_id'],request['category_uuid'])]
            tag=LibraryTag(request['tag_id'],request['name'],category.name,request['tag_uuid'],
                           category.category_id,category.source_uuid)
            following=[index for index,value in enumerate(result) if value.category_id==category.category_id]
            result.insert(following[-1]+1 if following else len(result),tag)
        return result

    def _assignments_pending(self,tag_id,tag_uuid):
        if tag_id is None:return False
        lib=self.window.lib;scope=(str(lib.source),lib.source_library_id)
        lane=getattr(self.tags,'tag_state_lane',None)
        jobs=[*(getattr(self.tags,'pending',()) or ())]
        jobs.extend(row.get('job',{}) for row in getattr(lane,'queue',()))
        jobs.extend(row.get('job',{}) for row,_error in getattr(lane,'failed',()))
        for job in jobs:
            if not isinstance(job,dict) or not isinstance(job.get('request'),dict):return True
            request=job['request']
            if ((job.get('source'),job.get('library_id'))==scope and request.get('tag_id')==tag_id
                    and (tag_uuid is None or request.get('tag_uuid')==tag_uuid)):return True
        legacy=getattr(self.tags,'_pending',None);source=getattr(self.tags,'lib',None)
        return bool(legacy and source and (str(source.source),source.source_library_id)==scope
            and legacy.tag_id==tag_id and (tag_uuid is None or legacy.tag_uuid==tag_uuid))

    def rename_reason(self,tag_id=None,tag_uuid=None):
        w=self.window;lib=w.lib
        if tag_id is not None and self.pending_creation(tag_id,tag_uuid):
            return self.pending_creation_reason(tag_id,tag_uuid)
        if self.queue_error():return self.queue_error()
        if any(self._ordering(job['request']) for job in self.pending):
            return 'Finish pending tag order changes before changing tag definitions'
        if self.ack.pending:return 'Finishing the saved tag change acknowledgement'
        if self.admission.error:return 'The pending tag intention needs retry or discard'
        if self.closing or w._shutting_down or w._state_maintenance:return 'Library operation in progress'
        if (not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified
                or not lib.source_library_id or not lib.source_tags):return 'Refresh the native library before renaming tags'
        if self._assignments_pending(tag_id,tag_uuid):
            return 'Finish or discard this tag’s pending assignments before renaming it'
        if not self.rename_factory and tag_rename.load_authority(
                self.tags.data_root/tag_rename.AUTHORITY_FILE,Path(lib.source),lib.source_library_id) is None:
            return 'Native tag renaming awaits separate acceptance for this library'
        return ''

    def enqueue_rename(self,request):
        tag_rename.validate_request(request)
        return self.enqueue(request)

    @classmethod
    def _validate(cls, request):
        return (tag_rename.validate_request(request) if cls._renaming(request) else
                tag_order.validate_request(request) if cls._ordering(request) else
                definitions.validate_deletion_request(request) if cls._deleting(request)
                else definitions.validate_request(request))

    def order_reason(self,*,pending=True):
        w=self.window;lib=w.lib
        if self.publication_retry_pending:return self._creation_publication_error
        if self.queue_error():return self.queue_error()
        if self.ack.error:return 'Finishing the saved tag change acknowledgement'
        if self.admission.error:return 'The pending tag intention needs retry or discard'
        if (self.admission.staged and (self.admission._scope!=self.admission._context()
                or not self.admission.current_owner())):
            return 'Return to the original library; restored state cannot accept an older intention'
        if (self.closing or w._shutting_down or w._state_maintenance or w._refresh_worker is not None
                or device_blocks_library_work(w)):return 'Library operation in progress'
        if (not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified
                or not lib.source_library_id or not lib.source_tag_categories):
            return 'Refresh the native library before reordering tags'
        if pending and self.pending:
            if any(not self._ordering(job['request']) for job in self.pending):
                return 'Finish pending tag changes before reordering tags'
            if any(job['error'] for job in self.pending):return 'Pending tag order changes need attention'
            if any((job['source'],job['library_id'])!=(str(lib.source),lib.source_library_id) for job in self.pending):
                return 'Switch to the original library to finish its tag changes'
        if pending and not self._project_order(lib.source_tags)[1]:
            return 'The source tag identities or order changed; refresh before dragging again'
        if not self.order_factory and tag_order.load_authority(
                self.tags.data_root/'rekordbox-tag-order-authority.json',Path(lib.source),lib.source_library_id) is None:
            return 'Native tag reordering awaits separate acceptance for this library'
        return ''

    def enqueue_order(self,request):
        tag_order.validate_request(request)
        return self.enqueue(request)

    @classmethod
    def _ordered_tags_match(cls,tags,request,*,after=False):
        by_id={tag.tag_id:tag for tag in tags}
        group=[tag for tag in tags if tag.category_id==request['category_id']]
        wanted=request['order'] if after else [row['id'] for row in request['before']]
        expected={row['id']:(row['uuid'],row['name']) for row in request['before']}
        return (len(by_id)==len(tags) and [tag.tag_id for tag in group]==wanted
                and all(tag.category_uuid==request['category_uuid']
                        and expected.get(tag.tag_id)==(tag.source_uuid,tag.name) for tag in group))

    @staticmethod
    def _renamed_tags_match(before,after,request):
        from dataclasses import replace
        wanted=(request['tag_id'],request['tag_uuid'],request['category_id'],request['category_uuid'])
        matches=[tag for tag in before.source_tags
                 if (tag.tag_id,tag.source_uuid,tag.category_id,tag.category_uuid)==wanted]
        if len(matches)!=1 or matches[0].name not in (request['name'],request['new_name']):return False
        expected=[replace(tag,name=request['new_name']) if tag is matches[0] else tag for tag in before.source_tags]
        if after.source_tags!=expected or after.source_playlist_catalog!=before.source_playlist_catalog:return False
        assignments={}
        for track in after.tracks_by_id.values():
            if track.source_uuid and track.source_tag_ids is not None:
                assignments.setdefault((track.native_id,track.source_uuid),set()).add(tuple(sorted(track.source_tag_ids)))
        for track in before.tracks_by_id.values():
            if track.source_uuid and track.source_tag_ids is not None:
                if assignments.get((track.native_id,track.source_uuid))!={tuple(sorted(track.source_tag_ids))}:return False
        smart={row['ID'] for row in before.source_playlist_catalog.get('nodes',()) if row.get('Attribute')==4}
        def memberships(library):
            return {playlist.pid:tuple(library.tracks_by_id[key].native_id if key in library.tracks_by_id else key
                        for key in playlist.keys) for playlist in library.playlists if playlist.pid in smart}
        return memberships(before)==memberships(after)

    def native_order(self,tags):
        """Project only this exact source's staged/durable native order intention."""
        return self._project_order(tags)[0]

    def _project_order(self,tags):
        """Return the visible order and whether its pending chain is still usable."""
        if self.queue_error():return list(tags),False
        lib=self.window.lib;result=list(tags);jobs=list(self.window.settings.get(QUEUE,[]))
        if (self.admission.staged and self.admission._scope==self.admission._context()
                and self.admission.current_owner()):jobs.extend(self.admission.rows)
        handoff=self._publication_handoff()
        if handoff is not None and self._ordering(handoff[0]['request']):
            bridge=handoff[0]
            if not any(job['request']['operation_id']==bridge['request']['operation_id'] for job in jobs):
                jobs.insert(0,bridge)
        for index,job in enumerate(jobs):
            request=job['request']
            if not self._ordering(request):break
            if (not lib or job['error']
                    or (job['source'],job['library_id'])!=(str(lib.source),lib.source_library_id)):
                return result,False
            # At most the FIFO head may have committed before its durable ACK.
            # A later before/after mismatch is an external change, not a basis
            # from which another gesture can append to the accepted chain.
            if index==0 and self._ordered_tags_match(result,request,after=True):continue
            if not self._ordered_tags_match(result,request):return result,False
            by_id={tag.tag_id:tag for tag in result};iterator=iter(request['order'])
            result=[by_id[next(iterator)] if tag.category_id==request['category_id'] else tag for tag in result]
        return result,True

    def deletion_reason(self):
        w = self.window
        if self.queue_error():
            return self.queue_error()
        if any(self._ordering(job['request']) for job in self.pending):
            return 'Finish pending tag order changes before changing tag definitions'
        if self.ack.pending:return 'Finishing the saved tag change acknowledgement'
        if self.admission.error:return 'The pending tag intention needs retry or discard'
        if self.closing or w._shutting_down or w._state_maintenance:
            return 'Library operation in progress'
        lib = w.lib
        if (not lib or lib.source_kind != 'rekordbox-db' or not self.tags.verified
                or not lib.source_library_id):
            return 'Refresh the rekordbox library before deleting tags'
        if not self.deletion_factory and definitions.load_deletion_authority(
                self.tags.data_root / 'rekordbox-tag-definition-delete-authority.json',
                Path(lib.source), lib.source_library_id) is None:
            return 'Deleting tags is not enabled for this library yet'
        if not lib.source_tags and not any(self._deleting(job['request']) for job in self.pending):
            return 'There are no My Tags to delete'
        return ''

    def enqueue_deletion(self, request):
        definitions.validate_deletion_request(request)
        return self.enqueue(request)

    def enqueue(self, request):
        self._validate(request)
        deleting = self._deleting(request)
        ordering = self._ordering(request)
        renaming = self._renaming(request)
        reason = (self.rename_reason(request['tag_id'],request['tag_uuid']) if renaming else self.order_reason() if ordering else
                  self.deletion_reason() if deleting else self.reason())
        if reason:
            self.window._flash_status(reason, 10)
            return False
        w = self.window
        if request['library_id'] != w.lib.source_library_id:
            w._flash_status('The selected library changed; capture the tag change again', 10)
            return False
        for job in self.pending:
            if job['request']['operation_id'] == request['operation_id']:
                if (job['source'],job['library_id'],job['request']) == (str(w.lib.source),w.lib.source_library_id,request):
                    return True
                w._flash_status('That operation identity is already used by another intention',10)
                return False
        if ordering:
            projected,valid=self._project_order(w.lib.source_tags)
            if not valid or not self._ordered_tags_match(projected,request):
                w._flash_status('The source tag identities or order changed; refresh before dragging again',10)
                return False
        elif deleting or renaming:
            matches = [tag for tag in w.lib.source_tags
                       if (tag.tag_id, tag.source_uuid, tag.category_id, tag.category_uuid, tag.name)
                       == tuple(request[key] for key in ('tag_id', 'tag_uuid', 'category_id', 'category_uuid', 'name'))]
            if len(matches) != 1:
                w._flash_status('The selected tag changed; refresh the library', 10)
                return False
            if renaming and any(tag.category_id==request['category_id'] and tag.tag_id!=request['tag_id']
                    and tag.name.casefold()==request['new_name'].casefold() for tag in w.lib.source_tags):
                w._flash_status('A tag with that name already exists in this category',10);return False
        elif any(tag.category_id == request['category_id'] and tag.name.casefold() == request['name'].casefold()
                 for tag in self.available_tags()):
            w._flash_status('A tag with that name already exists in this category', 10)
            return False
        for job in self.pending:
            if ordering and self._ordering(job['request']):continue
            if (job['request']['tag_id'] == request['tag_id'] or job['request']['tag_uuid'] == request['tag_uuid']
                    or (job['source'], job['library_id'], job['request']['category_id'], self._desired_name(job['request']).casefold())
                    == (str(w.lib.source), w.lib.source_library_id, request['category_id'], self._desired_name(request).casefold())):
                w._flash_status('That tag already has a pending change', 10)
                return False
        if len(self.pending) >= 256:
            w._flash_status('Wait for pending tag changes to finish', 10)
            return False
        job = dict(source=str(w.lib.source), library_id=w.lib.source_library_id,
                   request=deepcopy(request), error='')
        if not self.admission.stage(job):return False
        self.status(self._label(request),definitions_changed=True)
        self.admission.pump()
        self.schedule()
        return True

    def status(self, message, *, definitions_changed=False):
        if self.tags.definition_update_status != message or definitions_changed:
            self.tags.definition_update_status = message
            self.tags.changed.emit()

    def schedule(self):
        if self.closing or not self.pending or not self.admission.current_owner():
            return
        # An idle notification releases a shared source opportunity now. Do
        # not leave it behind the previous busy poll; explicit native-owner
        # backoff still owns its measured not_before deadline.
        self.timer.start(max(0, int((self.not_before - time.monotonic()) * 1000)))

    def pump(self):
        w = self.window
        if (self.closing or not self.pending or self.admission.pending or self.ack.pending
                or not self.admission.current_owner()):
            return
        if self.queue_error():
            self.status(self.queue_error())
            return
        if self.pending[0]['error']:
            return
        if (self.tags.source_job_owned or self.tags.prep_priority or w._refresh_worker is not None
                or w._state_maintenance or device_blocks_library_work(w)):
            self.timer.start(2000)
            return
        imports = getattr(w, 'shared_import_controller', None)
        if imports and imports.source_priority:
            self.timer.start(2000)
            return
        # order_reason's no-pending guard is for a new gesture. A saved exact
        # order head still rechecks its own independent authority on the worker.
        if (self.rename_reason(self.pending[0]['request']['tag_id'],self.pending[0]['request']['tag_uuid'])
                if self._renaming(self.pending[0]['request']) else
                self.order_reason(pending=False) if self._ordering(self.pending[0]['request']) else
                self.deletion_reason() if self._deleting(self.pending[0]['request']) else self.reason()):
            return
        if time.monotonic() < self.not_before:
            self.schedule()
            return
        job = deepcopy(self.pending[0])
        lib = w.lib
        submitted_scope=self.admission._context()
        if (job['source'], job['library_id']) != (str(lib.source), lib.source_library_id):
            self.status('Switch to the original library to finish its tag change')
            return

        def work():
            try:
                assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc) == 'Close rekordbox and its background agent before editing':
                    return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            if self._renaming(job['request']):
                authority=tag_rename.load_authority(self.tags.data_root/tag_rename.AUTHORITY_FILE,
                                                   Path(lib.source),lib.source_library_id)
                store=(self.rename_factory(lib) if self.rename_factory else
                       tag_rename.TagRenameStore(authority,self.tags._write_root(lib)))
            elif self._ordering(job['request']):
                authority=tag_order.load_authority(self.tags.data_root/'rekordbox-tag-order-authority.json',
                                                  Path(lib.source),lib.source_library_id)
                store=(self.order_factory(lib) if self.order_factory else
                       tag_order.TagOrderStore(authority,self.tags._write_root(lib)))
            elif self._deleting(job['request']):
                authority = definitions.load_deletion_authority(
                    self.tags.data_root / 'rekordbox-tag-definition-delete-authority.json',
                    Path(lib.source), lib.source_library_id)
                store = (self.deletion_factory(lib) if self.deletion_factory else
                         definitions.TagDeletionStore(authority, self.tags._write_root(lib)))
            else:
                authority = definitions.load_authority(
                    self.tags.data_root / 'rekordbox-tag-definition-authority.json',
                    Path(lib.source), lib.source_library_id)
                store = (self.factory(lib) if self.factory else
                         definitions.TagDefinitionStore(authority, self.tags._write_root(lib)))
            created = store.apply(job['request'])
            library = (self.loader(Path(lib.source)) if self.loader is not None
                       else self.tags.readback_library(lib))
            return dict(created=created, library=library)

        def completed(worker):
            if (submitted_scope[1:]!=self.admission._context()[1:]
                    or not self.admission.current_owner()):return
            if worker.error:
                self.fail(worker.error, job)
                return
            if worker.result.get('waiting'):
                self.not_before = time.monotonic() + 2
                self.status('Tag change pending — quit rekordbox to save')
                self.schedule()
                return
            head = self.pending[0] if self.pending else None
            if (not w.lib or (str(w.lib.source), w.lib.source_library_id) != (job['source'], job['library_id'])
                    or not isinstance(head, dict) or head.get('request') != job['request']):
                self.fail('Saved in the original library; switch back to finish the tag change', job)
                return
            library = worker.result['library']
            verified = (self._renamed_tags_match(lib,library,job['request']) if self._renaming(job['request']) else
                        self._ordered_tags_match(library.source_tags,job['request'],after=True)
                        if self._ordering(job['request']) else
                        not any(tag.tag_id == job['request']['tag_id'] for tag in library.source_tags)
                        and not any(job['request']['tag_id'] in (track.source_tag_ids or ())
                                    for track in library.tracks_by_id.values())
                        if self._deleting(job['request']) else
                        any(tag.tag_id == job['request']['tag_id'] and tag.source_uuid == job['request']['tag_uuid']
                               and tag.name == job['request']['name']
                               and tag.category_id == job['request']['category_id']
                               and tag.category_uuid == job['request']['category_uuid']
                            for tag in library.source_tags))
            if (library.source_kind != 'rekordbox-db'
                    or (str(library.source), library.source_library_id) != (job['source'], job['library_id']) or not verified):
                self.fail('The tag change could not be verified; the intention is preserved', job)
                return
            previous = w.settings.get(QUEUE, [])
            if self._creating(job['request']) or self._ordering(job['request']):
                self._creation_publication=(deepcopy(job),submitted_scope)
                self._creation_publication_error=''
            def current():
                return bool(w.lib and w.lib.source_kind=='rekordbox-db'
                    and (str(w.lib.source),w.lib.source_library_id)==(job['source'],job['library_id']))
            def acknowledged():
                self.not_before=0
                if not self.closing and not w._shutting_down:w._publish_created_library(library)
                if (self._creating(job['request']) or self._ordering(job['request'])) and not self.closing and not w._shutting_down:
                    self._creation_jobs()
                    if self._creation_publication is not None:
                        raise RuntimeError('The saved tag library publication is incomplete; retry the saved tag change')
                self.status(self._label(self.pending[0]['request']) if self.pending else '')
                self.schedule()
            def acknowledgement_failed(error):
                message='Saved in rekordbox; local acknowledgement needs retry: '+str(error)
                if self._creating(job['request']) or self._ordering(job['request']):self._creation_publication_error=message[:4096]
                queue=w.settings.get(QUEUE,[])
                if queue and (queue[0].get('source'),queue[0].get('library_id'),queue[0].get('request'))==(job['source'],job['library_id'],job['request']):
                    w.settings[QUEUE]=[{**queue[0],'error':message[:4096]},*queue[1:]]
                self.status(message);w._flash_status(message,15)
                if (self._creating(job['request']) or self._ordering(job['request'])) and not any(
                        queued['request']['operation_id']==job['request']['operation_id']
                        for queued in w.settings.get(QUEUE,[])):
                    QTimer.singleShot(0,self,self._record_publication_blocker)
            self.ack.start(previous,list(previous[1:]),acknowledged,acknowledgement_failed,current=current)

        if not self.tags._run_source_job('tag-definitions', work, completed):
            self.timer.start(2000)

    def fail(self, message, job):
        self.timer.stop()
        previous = deepcopy(self.window.settings.get(QUEUE, []))
        owned = next((index for index, current in enumerate(previous)
                      if isinstance(current, dict) and (current.get('source'), current.get('library_id'), current.get('request'))
                      == (job['source'], job['library_id'], job['request'])), None)
        if owned is not None:
            updated = list(previous)
            updated[owned] = {**updated[owned], 'error': message}
            staged=deepcopy(self.admission.staged)
            def recorded():self.status('Tag change needs attention — '+message)
            def failed(error):
                # Hold the exact queue and staged suffix behind this ACK. A
                # RAM attention marker does not advance their durable base.
                queue=self.window.settings.get(QUEUE,[])
                if queue==previous:self.window.settings[QUEUE]=deepcopy(updated)
                attention='Tag change needs attention — '+message+'; local save needs retry: '+str(error)
                self.status(attention);self.window._flash_status(attention,15)
            self.ack.start(previous,updated,recorded,failed,
                           staged_after=staged,expected_staged=staged)
        self.window._flash_status(message, 12)

    def retry(self):
        if self.publication_retry_pending and not self.ack.pending:
            # A gesture captured during the source write can still need its
            # own canonical admission. Finish that exact tail before the
            # publication retry; neither failure may starve the other's Retry.
            if self.admission.pending:
                return self.admission.retry() if self.admission.error else False
            return self._retry_publication()
        if self.ack.pending:return self.ack.retry()
        previous=self.window.settings.get(QUEUE,[])
        if (self.admission.pending and previous and previous[0].get('error')
                and not self.closing and not self.tags.owned and not self.queue_error()):
            # One explicit retry clears the durable failed head while retaining
            # the exact suffix. Admission resumes only after this edge is saved.
            staged=deepcopy(self.admission.staged)
            def recorded():
                self.not_before=0
                if self.admission.error:self.admission.retry()
                self.schedule()
            def failed(error):
                message='Retry NOT saved — '+str(error)
                self.status(message);self.window._flash_status(message,15)
            return self.ack.start(previous,[{**previous[0],'error':''},*previous[1:]],recorded,failed,
                                  staged_after=staged,expected_staged=staged)
        if self.admission.pending:
            accepted=self.admission.retry()
            if accepted:self.status('Saving pending tag intention…')
            return accepted
        if self.closing or self.tags.owned or not self.pending or self.queue_error():
            return False
        previous = self.window.settings.get(QUEUE, [])
        self.window.settings[QUEUE] = [{**previous[0], 'error': ''}, *previous[1:]]
        if not self.window._save_state():
            self.window.settings[QUEUE] = previous
            return False
        self.not_before = 0
        self.schedule()
        return True

    def discard(self):
        from .intent_review import capture_discard,discard_is_current
        smart=getattr(getattr(self.window,'playlist_controller',None),'has_pending_tag_creation_dependency',lambda _operation:False)
        if any(self._assignments_pending(job['request']['tag_id'],job['request']['tag_uuid'])
               or smart(job['request']['operation_id']) for job in self._creation_jobs()):
            self.window._flash_status('Discard this new tag’s pending assignments before discarding its creation',12)
            return False
        review=capture_discard(self)
        if review is None:return False
        w = self.window
        if QMessageBox.question(w, 'Discard pending tag changes',
                f'Discard {len(review.jobs)} pending tag change(s)?\nChanges already saved in rekordbox remain saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        if not discard_is_current(self,review):
            w._flash_status('A save started or the pending edits changed; review them and try again.',12)
            return False
        if self.ack.pending:return self.ack.discard()
        if self.admission.pending:return self.admission.discard()
        previous = w.settings.get(QUEUE, [])
        w.settings[QUEUE] = []
        if not w._save_state():
            w.settings[QUEUE] = previous
            return False
        self.timer.stop()
        self.status('')
        w._native_reconcile_source()
        return True

    def stop(self):
        self.closing = True
        self.timer.stop()
        self.admission.stop()
        self.ack.stop()

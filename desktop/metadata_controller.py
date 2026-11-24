"""Durable source metadata intentions on the one shared native writer lane."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QObject,QTimer

from soundbox import alias_intents,engine,metadata,metadata_provenance as provenance,mutation_domain,shared_imports,source_truth
from soundbox import rekordbox_metadata_store as native
from soundbox.rekordbox_write import WriteProofError,assert_rekordbox_closed
from soundbox.state_repository import VerificationChanged
from .task_owner import TaskOwner
from .workers import OneShotTask
from .tag_controller import device_blocks_library_work

KEY='pending_metadata_edits'
ACKS='metadata_receipt_acks'
PAUSE_KEY='metadata_background_pauses'
EDITING_DISABLED_REASON='Title and artist saving is unavailable for this library'
BACKGROUND_PAUSED_STATUS='Background song updates paused'
_JOB={'id','source','library_id','local_id','native_id','track_uuid','path','field','value','request','retry','error'}
_BUILDING='soundbox-source-truth-building-v1'


def _intent_value(value,field):
    if type(value) is not str or '\0' in value or len(value)>4096 or field=='title' and not value:
        raise ValueError('Invalid metadata intention value')


def _matches_track(job,track):
    if not job['track_uuid']:return job['path']==str(track.path)
    return (job['native_id'],job['track_uuid'],job['path'])==(track.native_id,track.source_uuid,
        track.source_native_path or str(track.path))


class MetadataController(QObject):
    def __init__(self,window,*,store_factory=None,loader=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=store_factory;self.loader=loader
        self.closing=False;self.not_before=0.0
        self.tasks=TaskOwner(self);self._truth_index=None
        self._truth_index_owner=None
        self._truth_observed_proof=None;self._truth_observed_error=None;self._truth_check_pending=False
        self._truth_observed_owners=();self._truth_error_owners=()
        self._truth_observed_epoch=None;self._truth_error_epoch=None
        self._truth_observed_source=None
        self._truth_static_proof=None
        self._truth_observed_progress=None
        self._truth_error_progress=None
        self._alias_validation=None;self._validated_origins=None
        self._derived_dirty={};self._derived_context=None;self._derived_epoch=0;self._derived_frozen=None
        self._state_commit_pending=False;self._state_commit_error='';self._state_commit_token=None
        self._attention_message=''
        self._retirement_ack=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule);self.tags.changed.connect(self.schedule)

    def _commit_background_state(self,before,replacements,*,removals=(),committed,failed,message):
        """Commit detached selected leaves; no source phase advances on admission.

        Older isolated hosts retain synchronous saves. Production restores the
        staged owners before dispatch, then its coordinator publishes only these
        leaves after the state worker's durable completion.
        """
        w=self.window;commit=getattr(w,'_commit_metadata_state',None);removals=tuple(removals)
        transfer=self._truth_progress_transfer(before,replacements,removals) if callable(commit) else None
        def restore():
            for name,value in before.items():setattr(w,name,value)
        if not callable(commit):
            if w._save_state():committed();return True
            restore();failed();return False
        restore();self._state_commit_pending=True;token=object();self._state_commit_token=token
        def saved():
            if self._state_commit_token is not token:return
            self._state_commit_token=None;self._state_commit_pending=False;self._state_commit_error=''
            if transfer is not None:
                stamp,epoch,progress=transfer;current=self._observed_truth()
                if (current is not None and self._truth_stamp(*current)==stamp and self._truth_epoch()==epoch
                        and self._truth_progress(current[0])==progress):self._truth_observed_progress=progress
            committed()
        def rejected(error):
            if self._state_commit_token is not token:return
            self._state_commit_token=None;self._state_commit_pending=False;self._state_commit_error=message
            self.timer.stop();self.status(message,attention=True)
        try:accepted=commit(replacements,removals,saved,rejected)
        except (OSError,ValueError,RuntimeError) as exc:
            rejected(exc);return False
        if not accepted:rejected('State commit was not admitted')
        return accepted

    def _pause_error(self):
        pauses=self.window.settings.get(PAUSE_KEY,[])
        try:
            if not isinstance(pauses,list) or len(pauses)>16:raise ValueError('Pause scopes')
            seen=set()
            for scope in pauses:
                source_truth._validate_scope(scope)
                identity=source_truth.scope_key(scope)
                if scope['kind']!='rekordbox-db' or identity in seen:raise ValueError('Pause binding')
                seen.add(identity)
        except (ValueError,TypeError,KeyError):
            return 'Saved background song-update pause needs attention; intentions are preserved'
        return ''

    @property
    def background_paused(self):
        """An exact source choice, independent of every native write permission."""
        lib=self.window.lib
        if not lib or lib.source_kind!='rekordbox-db' or self._pause_error():return False
        return source_truth.scope(lib) in self.window.settings.get(PAUSE_KEY,[])

    def set_background_paused(self,paused):
        """Persist one scheduling choice; accepted FIFO work keeps its ownership."""
        w=self.window;lib=w.lib
        if (type(paused) is not bool or self._state_commit_pending or self.closing or w._shutting_down or w._state_maintenance
                or getattr(w,'_state_publication',False) or not lib or lib.source_kind!='rekordbox-db'
                or not self.tags.verified):return False
        error=self._pause_error()
        if error:w._flash_status(error,12);return False
        scope=source_truth.scope(lib);previous=w.settings.get(PAUSE_KEY)
        values=w.settings.get(PAUSE_KEY,[])
        if (scope in values)==paused:return True
        updated=[*values,scope] if paused else [value for value in values if value!=scope]
        if len(updated)>16:w._flash_status('Too many saved library pause choices',12);return False
        if updated:w.settings[PAUSE_KEY]=updated
        else:w.settings.pop(PAUSE_KEY,None)
        if not w._save_state():
            if previous is None:w.settings.pop(PAUSE_KEY,None)
            else:w.settings[PAUSE_KEY]=previous
            return False
        self.timer.stop()
        self.status(BACKGROUND_PAUSED_STATUS if paused else
            'Metadata saves pending' if self.pending or self.migration_pending else '')
        self.schedule();return True

    def _paused_progress_pending(self):
        descriptor=self.window.settings.get(source_truth.MIGRATION)
        active=descriptor.get('active') if isinstance(descriptor,dict) else None
        return bool(isinstance(active,dict) and not descriptor.get('error'))

    def _paused_status(self):
        if self._state_commit_error:return self._state_commit_error
        for key in (alias_intents.NEXT,source_truth.MIGRATION):
            plan=self.window.settings.get(key)
            if isinstance(plan,dict) and plan.get('error'):return plan['error']
        return BACKGROUND_PAUSED_STATUS

    def _show_paused_status(self):
        message=self._paused_status()
        self.status(message,attention=message!=BACKGROUND_PAUSED_STATUS)

    @property
    def background_summary(self):
        """Cheap menu-only progress; never revalidate or scan a library to draw it."""
        if self.window.lib is None:
            return ''
        descriptor = self.window.settings.get(source_truth.MIGRATION)
        if isinstance(descriptor, dict) and descriptor.get('scope') == source_truth.scope(self.window.lib):
            order = descriptor.get('order')
            cursor = descriptor.get('cursor')
            if isinstance(order, list) and type(cursor) is int and 0 <= cursor <= len(order):
                phase = 'Paused' if self.background_paused else 'Background'
                if descriptor.get('error') or self._state_commit_error or self._attention_message:
                    phase = 'Needs attention'
                elif cursor == len(order) and descriptor.get('active') is None:
                    phase = 'Complete'
                return f'Song updates: {cursor:,} of {len(order):,} · {phase}'
        if self._attention_message or self._state_commit_error:
            return 'Song updates: Needs attention'
        if self.pending:
            return f'Song details: {len(self.pending):,} save(s) pending'
        return ''

    @property
    def retirement_pending(self):
        return bool(self._retirement_error() or self.window.settings.get(ACKS))

    def _retirement_error(self):
        ledger=self.window.settings.get(ACKS,[])
        try:
            if not isinstance(ledger,list) or len(ledger)>256:raise ValueError('Retirement ledger')
            seen=set()
            for row in ledger:
                if not isinstance(row,dict) or set(row)!={'entry','error'} or type(row['error']) is not str:
                    raise ValueError('Retirement record')
                native.validate_retirement(row['entry']);identity=(row['entry']['source'],row['entry']['request']['operation_id'])
                if identity in seen:raise ValueError('Duplicate retirement')
                seen.add(identity)
        except (ValueError,TypeError,KeyError,WriteProofError):return 'Saved metadata receipt retirements need attention'
        return ''

    def _resume_receipt_retirement(self):
        w=self.window;ledger=w.settings.get(ACKS,[])
        if (self.closing or w._shutting_down or w._state_maintenance or getattr(w,'_state_publication',False)
                or self._background_state_busy() or self._retirement_error() or not ledger or ledger[0]['error']):return
        if self._retirement_ack is not None:
            captured,result=self._retirement_ack;self._retirement_ack=None
            if ledger[0]!=captured:return
            error=str(result) if isinstance(result,Exception) else ''
            updated=[{**captured,'error':error},*ledger[1:]] if error else ledger[1:]
            before=w.settings;w.settings={**before,ACKS:updated}
            def committed():
                if error:self.status('Metadata receipt retirement needs attention',attention=True)
                else:self.schedule()
            def failed():
                w.settings[ACKS]=[{**captured,'error':error or 'Compacted receipt acknowledgement could not be saved'},*ledger[1:]]
                self.status('Metadata receipt retirement needs storage attention',attention=True)
            self._commit_background_state({'settings':before},{('settings',ACKS):updated},
                committed=committed,failed=failed,message='Metadata receipt retirement needs storage attention')
            return
        if self.tasks.pending:return
        captured=deepcopy(ledger[0]);entry=captured['entry']
        worker=OneShotTask(lambda:native.retire_acknowledged_receipt(self.tags.data_root,entry),
                           log_label='Acknowledged metadata receipt retirement')
        def completed(task):
            current=w.settings.get(ACKS,[])
            if not current or current[0]!=captured:return
            self._retirement_ack=(captured,task.result)
            if self._background_state_busy() or getattr(w,'_state_publication',False):self.schedule()
            else:self._resume_receipt_retirement()
        self.tasks.adopt(worker,completed);worker.start()

    def _background_state_busy(self):
        w=self.window
        return bool(self.tags.source_job_owned or self.tags.prep_priority or w._refresh_worker is not None
                    or device_blocks_library_work(w))

    def stage_source_scope(self,incoming,*,previous=None,bootstrap=False):
        """Stage RAM only; caller saves source selection and curation together.

        Return the exact owner token to rollback_source_scope if that ONE save
        fails. No source/library publication occurs here.
        """
        w=self.window
        fields={'title_overrides':'titles','artist_overrides':'overrides','artist_aliases':'aliases',
                'custom':'custom','playlist_removals':'removals','playlist_additions':'playlist_additions'}
        document={key:getattr(w,attr) for key,attr in fields.items()}
        document['hidden_tracks']={key:w.hidden_dates.get(key,'') for key in w.hidden}
        document['settings']=w.settings
        staged=source_truth.stage_scope(document,incoming,previous=previous,bootstrap=bootstrap)
        token={attr:getattr(w,attr) for attr in (*fields.values(),'hidden','hidden_dates','settings')}
        for key,attr in fields.items():setattr(w,attr,staged[key])
        w.hidden_dates=staged['hidden_tracks'];w.hidden=set(w.hidden_dates);w.settings=staged['settings']
        return token

    def rollback_source_scope(self,token):
        for attr,value in token.items():setattr(self.window,attr,value)

    def _stage_ack_source_curation(self,incoming,replacements,removals,*,pin_index=None):
        """Commit changed curation pins with the overlays that own them.

        Publication repeats the normal source guards. Its header must already
        describe the durable ACK, otherwise retiring one mask causes a second
        whole-state GUI save during publication.
        """
        w=self.window
        if w.lib is None or source_truth.scope(w.lib)!=source_truth.scope(incoming):
            raise ValueError('Metadata acknowledgement cannot retarget saved curation')
        previous=w.settings.get(source_truth.CURATION)
        if previous is not None and previous.get('active',{}).get('scope')!=source_truth.scope(incoming):
            raise ValueError('Metadata acknowledgement differs from the saved curation source')
        fields={'title_overrides':'titles','artist_overrides':'overrides','artist_aliases':'aliases',
                'custom':'custom','playlist_removals':'removals','playlist_additions':'playlist_additions'}
        document={key:getattr(w,attr,{}) for key,attr in fields.items()}
        document['hidden_tracks']={key:getattr(w,'hidden_dates',{}).get(key,'') for key in getattr(w,'hidden',())}
        document['settings']=w.settings
        current=source_truth.stage_scope(document,incoming,previous=w.lib,
                                        pin_index=pin_index)['settings'][source_truth.CURATION]
        if current==previous:return
        w.settings[source_truth.CURATION]=current
        root=('settings',source_truth.CURATION)
        if (previous is None or current['format']!=previous['format']
                or current['partitions']!=previous['partitions']
                or current['active']['scope']!=previous['active']['scope']):
            replacements[root]=current;return
        before=previous['active'];after=current['active'];missing=object()
        for namespace in ('pins','playlist_pins'):
            for identity in before[namespace].keys()|after[namespace].keys():
                old=before[namespace].get(identity,missing);new=after[namespace].get(identity,missing)
                if old==new:continue
                path=(*root,'active',namespace,identity)
                if new is missing:removals.append(path)
                else:replacements[path]=new
        for namespace in ('unbound','unbound_playlists'):
            if before[namespace]!=after[namespace]:replacements[(*root,'active',namespace)]=after[namespace]

    def stage_native_refresh(self,incoming,*,changed_ids,verified=True):
        """Stage sparse ACK reconciliation; caller saves before publication.

        The refresh owner supplies its existing changed-row inventory. An empty
        inventory does not walk the library or provenance. Cached reads cannot
        retire local edits. The returned owner token supports exact rollback.
        """
        w=self.window
        if not verified or not changed_ids:return None
        if (incoming is None or incoming.source_kind!='rekordbox-db' or w.lib is None
                or source_truth.scope(incoming)!=source_truth.scope(w.lib)):return None
        scope=source_truth.scope(incoming);existing=w.settings.get(alias_intents.ORIGINS,{})
        origins=existing;titles=w.titles;artists=w.overrides
        copies={};pending={};junk=None;junk_ready=False
        for job in self.pending:
            if (job['source'],job['library_id'])==(scope['source'],scope['library_id']):
                pending.setdefault(job['local_id'],[]).append(job)
        def copy_map(field,values):
            if field not in copies:copies[field]=provenance.copy_values(values)
            return copies[field]
        for local in changed_ids:
            track=incoming.tracks_by_id.get(local)
            if not track or not track.source_uuid or local in incoming.source_binding_issues:continue
            identity=alias_intents.key(scope,dict(local_id=local,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            origin=existing.get(identity)
            if origin is None:continue
            alias_intents.validate_origins({identity:origin})
            if not junk_ready:junk=w._title_junk();junk_ready=True
            owned=[(job['field'],job['value']) for job in pending.get(local,[]) if _matches_track(job,track)]
            revised,titles,artists=provenance.reconcile(origin,title=track.name,artist=track.artist,
                titles=titles,artists=artists,owned=owned,junk=junk,copy_map=copy_map)
            if revised is not origin:
                if origins is existing:origins=dict(existing)
                origins[identity]=revised
        if origins is existing and titles is w.titles and artists is w.overrides:return None
        token=dict(settings=w.settings,titles=w.titles,overrides=w.overrides)
        w.settings={**w.settings,alias_intents.ORIGINS:origins};w.titles=titles;w.overrides=artists
        return token

    def publish_derived(self,incoming,*,changed_ids,context,verified=True):
        """Coalesce native dirty IDs; derive scalars on the existing worker lane.

        ``context`` is a pair of worker-built FrozenArtistRegistry instances:
        current aliases, then unaliased. No-op refresh/own ACK passes no IDs.
        """
        if (not verified or not changed_ids or self.closing or self.window._shutting_down
                or incoming is not self.window.lib or incoming.source_kind!='rekordbox-db'):return False
        if (not isinstance(context,tuple) or len(context)!=2
                or any(not isinstance(item,(metadata.FrozenArtistRegistry,metadata.ArtistRegistry)) for item in context)):
            raise ValueError('Refresh metadata requires immutable worker registry context')
        scope=source_truth.scope(incoming)
        if self._derived_context is not None and self._derived_context[0]!=scope:self._derived_dirty={}
        self._derived_context=(scope,context);self._derived_epoch+=1
        for local in changed_ids:self._derived_dirty[local]=self._derived_epoch
        self.status('Metadata updates pending');self.schedule();return True

    @property
    def planning_pending(self):return bool(self._derived_dirty)

    def _resume_derived(self):
        w=self.window
        if (not self._derived_dirty or self._derived_context is None or self.tasks.pending or self.closing
                or w._shutting_down or w._state_maintenance or getattr(w,'_state_publication',False)
                or not self.tags.verified or not w.lib or self.queue_error() or self.background_paused):return False
        scope,context=self._derived_context
        if source_truth.scope(w.lib)!=scope:return False
        if w.settings.get(alias_intents.NEXT) is not None:return False
        main=w.settings.get(source_truth.MIGRATION)
        if main and (main.get('format')==_BUILDING or main.get('active') is not None
                     or main['cursor']<len(main['order']) or main.get('error')):return False
        selected={};inputs=[];original_inputs={};origins=w.settings.get(alias_intents.ORIGINS,{})
        missing=[]
        for local,epoch in self._derived_dirty.items():
            track=w.lib.tracks_by_id.get(local)
            if track is None:missing.append(local);continue
            # Registration owns these drafts; its verified native read will
            # supply the UUID and a dirty row. A global trust change must not
            # manufacture a second unresolved import intention meanwhile.
            if track.imported and not track.source_uuid:missing.append(local);continue
            if any(job['local_id']==local for job in self.pending):continue
            row=dict(scope=scope,local_id=local,native_id=track.native_id,uuid=track.source_uuid,
                path=track.source_native_path or str(track.path),name=track.name,artist=track.artist,
                stem=track.path.stem,native_file_type=track.info.get('nativeFileType'),
                native_analysis_flags=track.info.get('nativeAnalysisFlags'),
                issue=('Native identity is unavailable' if not track.source_uuid else
                       'Native binding needs reconciliation' if local in w.lib.source_binding_issues else ''))
            identity=alias_intents.key(scope,row);selected[local]=epoch
            inputs.append((row,deepcopy(origins.get(identity)),w.titles.get(local),w.overrides.get(local),
                {field:value for field,value in w.settings.get('track_info',{}).get(local,{}).items()
                 if field in ('title','artist')}))
            original_inputs[local]=inputs[-1]
        for local in missing:self._derived_dirty.pop(local)
        if not inputs:
            if not self.planning_pending and getattr(self.tags,'metadata_update_status','')=='Metadata updates pending':
                self.status('')
            return False
        junk=w._title_junk();identity=uuid4().hex;cached_context=self._derived_frozen
        context_owner=self._derived_context
        policy=(tuple(w.settings.get('title_rules_off',())),tuple(w.settings.get('title_words',())))
        def build():
            frozen=(cached_context[1] if cached_context is not None and
                all(a is b for a,b in zip(cached_context[0],context)) else
                tuple(metadata.freeze_registry(registry) for registry in context))
            result=[]
            for row,origin,title,artist,info in inputs:
                desired=provenance.derive(row,origin=origin,title_override=title,artist_override=artist,
                    info=info,context=frozen,junk=junk)
                result.append((row,desired))
            return result,source_truth._sha(inputs),frozen
        def completed(task):
            if (self.closing or w._shutting_down or not w.lib or source_truth.scope(w.lib)!=scope
                    or getattr(w,'_state_publication',False)):return
            if self.background_paused:self.status(BACKGROUND_PAUSED_STATUS);return
            if isinstance(task.result,Exception):self.status(str(task.result),attention=True);self.timer.start(2000);return
            if w.settings.get(alias_intents.NEXT) is not None:self.schedule();return
            if (self._derived_context is not context_owner or
                    policy!=(tuple(w.settings.get('title_rules_off',())),tuple(w.settings.get('title_words',())))):
                self.schedule();return
            current_main=w.settings.get(source_truth.MIGRATION)
            if current_main is not main:self.schedule();return
            targets={};current_origins=w.settings.get(alias_intents.ORIGINS,{})
            revised=current_origins;accepted=[]
            results,input_sha,frozen=task.result;self._derived_frozen=(context,frozen)
            for row,(title,artist,shown,origin) in results:
                local=row['local_id'];track=w.lib.tracks_by_id.get(local);key=alias_intents.key(scope,row)
                if (self._derived_dirty.get(local)!=selected[local] or track is None
                        or (track.native_id,track.source_uuid,track.source_native_path or str(track.path),track.name,track.artist)
                        !=(row['native_id'],row['uuid'],row['path'],row['name'],row['artist'])
                        or bool(row['issue'])!=bool(not track.source_uuid or local in w.lib.source_binding_issues)
                        or w.settings.get(alias_intents.ORIGINS,{}).get(key)!=origins.get(key)
                        or w.titles.get(local)!=original_inputs[local][2]
                        or w.overrides.get(local)!=original_inputs[local][3]
                        or {field:value for field,value in w.settings.get('track_info',{}).get(local,{}).items()
                            if field in ('title','artist')}!=original_inputs[local][4]):continue
                accepted.append(local)
                if origin!=current_origins.get(key):
                    if revised is current_origins:revised=dict(current_origins)
                    revised[key]=origin
                fields=[field for field,value in (('title',title),('artist',artist)) if row['name' if field=='title' else 'artist']!=value]
                if not fields and not row['issue']:continue
                targets[key]=dict(format='soundbox-source-truth-v1',migration_id=identity,scope=scope,
                    **{field:row[field] for field in ('local_id','native_id','uuid','path','native_file_type','native_analysis_flags')},
                    title=title,artist=artist,display_artist=shown,fields=fields,info={},error=row['issue'],
                    curation={field:provenance.snapshot(origin,field,w.titles if field=='title' else w.overrides,
                              original_inputs[local][4].get(field))
                              for field in ('title','artist')})
            if not accepted:self.schedule();return
            order=[key for key,row in targets.items() if not row['error']]
            unresolved=[key for key,row in targets.items() if row['error']]
            if main:
                for key in main['unresolved']:
                    targets[key]={**w.settings[source_truth.TARGETS][key],'migration_id':identity};unresolved.append(key)
            plan=dict(format='soundbox-source-truth-v1',id=identity,scope=scope,inputs_sha256=input_sha,
                order=order,unresolved=unresolved,cursor=0,active=None,counts={})
            before=w.settings
            if targets or revised is not current_origins:w.settings={**before,alias_intents.ORIGINS:revised}
            if targets:
                w.settings.update({alias_intents.NEXT:dict(format=alias_intents.FORMAT,id=identity,scope=scope,
                    state='ready',inputs_sha256=plan['inputs_sha256'],edits={},error='',plan=plan),alias_intents.TARGETS:targets})
            if w.settings is not before and not w._save_state():
                w.settings=before;self.status('Derived metadata needs storage attention',attention=True);self.timer.start(2000);return
            for local in accepted:
                if self._derived_dirty.get(local)==selected[local]:self._derived_dirty.pop(local)
            if not targets and not self.planning_pending and getattr(self.tags,'metadata_update_status','')=='Metadata updates pending':
                self.status('')
            self.schedule()
        worker=OneShotTask(build,log_label='Changed native metadata derivation')
        self.tasks.adopt(worker,completed);worker.start();return True

    @property
    def migration_pending(self):
        if self.planning_pending:return True
        if self.window.settings.get(alias_intents.NEXT) is not None:return True
        descriptor=self.window.settings.get(source_truth.MIGRATION)
        if descriptor is None:return False
        if self._migration_error():return True
        return (descriptor.get('format')==_BUILDING or descriptor.get('active') is not None
                or descriptor['cursor']<len(descriptor['order']) or bool(descriptor['unresolved']))

    def _observed_truth(self):
        """Observe durable plan containers once, retaining nested mutation detection.

        The small descriptor changes for ACKs and cursor progress retain these
        containers. No semantic validation or target copying happens on those
        GUI turns. Plain isolated hosts keep the original full validation path.
        """
        w=self.window;descriptor=w.settings.get(source_truth.MIGRATION)
        if (not callable(getattr(w,'_commit_metadata_state',None)) or not isinstance(descriptor,dict)
                or descriptor.get('format')==_BUILDING):return None
        targets=w.settings.get(source_truth.TARGETS)
        if not isinstance(targets,dict):return None
        for name,kind in (('order',list),('unresolved',list),('scope',dict)):
            if not isinstance(descriptor.get(name),kind):return None
        observed_targets=mutation_domain.observe(targets)
        containers={name:mutation_domain.observe(descriptor[name]) for name in ('order','unresolved','scope')}
        if observed_targets is not targets or any(containers[name] is not descriptor[name] for name in containers):
            descriptor={**descriptor,**containers}
            w.settings={**w.settings,source_truth.MIGRATION:descriptor,source_truth.TARGETS:observed_targets}
        return descriptor,observed_targets

    @staticmethod
    def _truth_stamp(descriptor,targets):
        # Scalar descriptor identity is checked even if a caller edits its
        # ordinary dict in place; containers carry recursive revision stamps.
        return (*(item for name in ('format','id','inputs_sha256')
                  for item in (type(descriptor.get(name)),descriptor.get(name))),
            *(value for name in ('order','unresolved','scope')
              for value in (id(descriptor[name]),descriptor[name].revision)),id(targets),targets.revision)

    def _truth_epoch(self):
        repository=getattr(self.window,'_state_repository',None)
        return (repository,getattr(repository,'generation',None),getattr(getattr(repository,'area',None),'generation',None))

    def _truth_source_context(self):
        library=self.window.lib
        return (str(library.source),library.source_kind,library.source_library_id) if library else None

    @staticmethod
    def _static_truth_identity(descriptor,targets,context):
        if context is None:return None
        owners=(targets,*(descriptor.get(name) for name in ('order','unresolved','scope')))
        if tuple(type(value) for value in owners)!=(mutation_domain.FrozenDict,
                mutation_domain.FrozenList,mutation_domain.FrozenList,mutation_domain.FrozenDict):return None
        scope=owners[3]
        if context[1]!=(scope.get('source'),scope.get('kind'),scope.get('library_id')):return None
        scalars=tuple((type(descriptor.get(name)),descriptor.get(name))
                      for name in ('format','id','inputs_sha256'))
        return context,scalars,owners

    @classmethod
    def _same_static_truth(cls,proof,descriptor,targets,context):
        current=cls._static_truth_identity(descriptor,targets,context)
        return bool(proof is not None and current is not None and proof[:2]==current[:2]
                    and all(left is right for left,right in zip(proof[2],current[2],strict=True)))

    @staticmethod
    def _truth_progress(descriptor):
        cursor=descriptor.get('cursor');active=descriptor.get('active')
        if type(cursor) is not int:raise ValueError('Invalid frozen progress')
        if active is None:return cursor,None
        if not isinstance(active,dict) or not isinstance(active.get('operations'),dict) or len(active['operations'])>2:
            raise ValueError('Invalid frozen active pair')
        return cursor,(active.get('target'),tuple(sorted(active['operations'].items())))

    def _truth_progress_transfer(self,before,replacements,removals):
        """Carry a proven domain only across the exact durable small progress delta."""
        old=before.get('settings',{});descriptor=old.get(source_truth.MIGRATION)
        targets=old.get(source_truth.TARGETS)
        if not isinstance(descriptor,dict) or not isinstance(targets,mutation_domain.ObservedDict):return None
        try:
            stamp=self._truth_stamp(descriptor,targets);epoch=self._truth_epoch()
            if (stamp!=self._truth_observed_proof or epoch!=self._truth_observed_epoch
                    or self._truth_observed_source!=self._truth_source_context()
                    or self._truth_progress(descriptor)!=self._truth_observed_progress):return None
            expected={**descriptor,'active':deepcopy(descriptor.get('active'))}
            root=('settings',source_truth.MIGRATION)
            for path,value in replacements.items():
                if path[:2]!=root:continue
                if path[2:] in (('cursor',),('active',)):expected[path[2]]=deepcopy(value)
                elif path[2:]==('active','operations') and expected['active'] is not None:
                    expected['active']['operations']=deepcopy(value)
                else:return None
            if any(path[:2]==root for path in removals):return None
            current=self.window.settings.get(source_truth.MIGRATION)
            current_targets=self.window.settings.get(source_truth.TARGETS)
            if self._truth_stamp(current,current_targets)!=stamp or self._truth_progress(current)!=self._truth_progress(expected):return None
            return stamp,epoch,self._truth_progress(expected)
        except (ValueError,KeyError,TypeError,AttributeError):return None

    @staticmethod
    def _truth_saved_values(descriptor,targets):
        return {('settings',source_truth.TARGETS):targets,**{
            ('settings',source_truth.MIGRATION,name):descriptor[name]
            for name in ('format','id','inputs_sha256','order','unresolved','scope','cursor','active')}}

    @classmethod
    def _snapshot_truth(cls,descriptor,targets,stamp,*,proof=None,context=None):
        if cls._truth_stamp(descriptor,targets)!=stamp:return None
        try:
            frozen={name:descriptor.get(name) for name in ('format','id','inputs_sha256','cursor','active')}
            frozen.update({name:descriptor[name].frozen_snapshot() for name in ('order','unresolved','scope')})
            detached=targets.frozen_snapshot()
        except mutation_domain.MutationChanged:return None
        except (RuntimeError,KeyError):
            if cls._truth_stamp(descriptor,targets)!=stamp:return None
            raise
        if cls._truth_stamp(descriptor,targets)!=stamp:return None
        if cls._same_static_truth(proof,frozen,detached,context):source_truth.validate_progress(frozen)
        else:source_truth.validate(frozen,detached)
        return frozen,detached

    def _ensure_truth_validation(self):
        try:observed=self._observed_truth()
        except (ValueError,TypeError):
            self.status('Saved SoundBox source truth needs attention; all intentions are preserved',attention=True);return False
        if observed is None:
            self._truth_static_proof=None
            return True
        descriptor,targets=observed;stamp=self._truth_stamp(descriptor,targets);epoch=self._truth_epoch()
        context=(epoch,self._truth_source_context())
        if self._truth_static_proof is not None and self._truth_static_proof[0]!=context:
            self._truth_static_proof=None
        progress=self._truth_progress(descriptor)
        if (self._truth_observed_proof==stamp and self._truth_observed_epoch==epoch
                and self._truth_observed_source==self._truth_source_context()
                and self._truth_observed_progress==progress):return True
        if (self._truth_observed_error and self._truth_observed_error[0]==stamp and self._truth_error_epoch==epoch
                and self._truth_error_progress==progress):
            self.status(self._truth_observed_error[1],attention=True);return False
        if self._truth_check_pending or self.tasks.pending:return False
        self._truth_check_pending=True
        captured={**descriptor,'active':deepcopy(descriptor.get('active'))}
        def validate():
            # Snapshotting and complete validation run on the worker. A change
            # during snapshot invalidates its version; live GUI containers are
            # never passed to the semantic validator.
            checked=self._snapshot_truth(captured,targets,stamp,proof=self._truth_static_proof,context=context)
            if checked is None:return None
            proof=epoch[0].verify_selected_values(self._truth_saved_values(*checked))
            if not epoch[0].current_verification(proof):return None
            return stamp,proof,self._static_truth_identity(*checked,context)
        worker=OneShotTask(validate,log_label='Frozen metadata intention validation')
        def completed(task):
            self._truth_check_pending=False
            if self.closing or self.window._shutting_down or self.window._state_maintenance:return
            current=self._observed_truth()
            if (current is None or self._truth_stamp(*current)!=stamp or self._truth_epoch()!=epoch
                    or (self._truth_epoch(),self._truth_source_context())!=context
                    or self._truth_progress(current[0])!=progress):
                self.schedule();return
            if isinstance(task.result,Exception):
                if isinstance(task.result,VerificationChanged):self.schedule();return
                self._truth_observed_error=(stamp,'Saved SoundBox source truth needs attention; all intentions are preserved')
                self._truth_error_owners=(targets,*(captured[name] for name in ('order','unresolved','scope')))
                self._truth_error_epoch=epoch
                self._truth_error_progress=progress
                self._truth_static_proof=None
                self.status(self._truth_observed_error[1],attention=True);return
            if task.result is not None and task.result[0]==stamp and epoch[0].current_verification(task.result[1]):
                self._truth_static_proof=task.result[2]
                self._truth_observed_source=context[1]
                self._truth_observed_proof=stamp;self._truth_observed_error=None
                self._truth_observed_owners=(targets,*(captured[name] for name in ('order','unresolved','scope')))
                self._truth_error_owners=()
                self._truth_observed_epoch=epoch;self._truth_error_epoch=None;self._truth_error_progress=None
                self._truth_observed_progress=progress
            self.schedule()
        self.tasks.adopt(worker,completed);worker.start();return False

    def _migration_error(self,*,force_truth=False):
        pause_error=self._pause_error()
        if pause_error:return pause_error
        try:
            origins=self.window.settings.get(alias_intents.ORIGINS,{})
            next_plan=self.window.settings.get(alias_intents.NEXT)
            targets=self.window.settings.get(alias_intents.TARGETS)
            identity=(id(origins),id(next_plan),id(targets))
            if origins is not self._validated_origins:
                alias_intents.validate_origins(origins);self._validated_origins=origins
            if self._alias_validation!=identity:
                if next_plan is not None:alias_intents.validate_next(next_plan,targets)
                self._alias_validation=identity
        except (ValueError,KeyError,TypeError,AttributeError):return 'Saved artist alias intentions need attention; all values are preserved'
        descriptor=self.window.settings.get(source_truth.MIGRATION)
        if descriptor is None:return ''
        targets=self.window.settings.get(source_truth.TARGETS)
        try:
            if not isinstance(descriptor,dict) or not isinstance(targets,dict):raise ValueError('Shape')
            if descriptor.get('format')==_BUILDING:
                if (not isinstance(descriptor.get('input_key'),str) or not isinstance(descriptor.get('scope'),dict)
                        or not isinstance(descriptor.get('edits'),dict) or not isinstance(descriptor.get('error'),str)
                        or not isinstance(targets.get(descriptor['input_key']),dict)):raise ValueError('Frozen inputs')
                source_truth._validate_scope(descriptor['scope'])
                record=targets[descriptor['input_key']]
                if record.get('format')!=_BUILDING or not isinstance(record.get('payload'),dict):raise ValueError('Frozen payload')
                for local,edit in descriptor['edits'].items():
                    if (type(local) is not str or not isinstance(edit,dict)
                            or any(type(edit.get(field)) is not str for field in ('uuid','path','title','artist','display_artist'))
                            or not isinstance(edit.get('fields'),list) or set(edit['fields'])-{'title','artist'}):
                        raise ValueError('Frozen later edit')
                for field,length in (('id',32),('inputs_sha256',64)):
                    value=descriptor.get(field)
                    if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
                        raise ValueError('Frozen input identity')
            else:
                order=descriptor.get('order');cursor=descriptor.get('cursor')
                if not isinstance(order,list) or type(cursor) is not int or not 0<=cursor<=len(order):raise ValueError('Progress')
                active=descriptor.get('active')
                if active is not None and (not isinstance(active,dict) or cursor>=len(order)
                        or active.get('target')!=order[cursor] or not isinstance(active.get('operations'),dict)):
                    raise ValueError('Active pair')
                if active is not None:
                    operations=active['operations']
                    if (len(operations)>2 or set(operations)-{'title','artist'}
                            or any(type(value) is not str or len(value)!=32
                                   or any(c not in '0123456789abcdef' for c in value)
                                   for value in operations.values())):raise ValueError('Active operation')
                observed=self._observed_truth()
                if observed is not None:
                    descriptor,targets=observed;stamp=self._truth_stamp(descriptor,targets)
                    if (self._truth_observed_error and self._truth_observed_error[0]==stamp
                            and self._truth_error_epoch==self._truth_epoch()
                            and self._truth_error_progress==self._truth_progress(descriptor)):
                        return self._truth_observed_error[1]
                    if (stamp==self._truth_observed_proof and self._truth_epoch()==self._truth_observed_epoch
                            and self._truth_progress(descriptor)!=self._truth_observed_progress):
                        raise ValueError('Frozen progress changed outside its durable continuation')
                else:
                    # Mutable fixture/legacy hosts have no revision proof.
                    # Their full validator must see in-place changes too.
                    source_truth.validate(descriptor,targets)
        except (ValueError,KeyError,TypeError,AttributeError):return 'Saved SoundBox source truth needs attention; all intentions are preserved'
        return ''

    def begin_source_truth(self):
        """Durably freeze effective inputs before any migration source writes."""
        w=self.window;lib=w.lib
        if (self.closing or w._shutting_down or w._state_maintenance or getattr(w,'_state_publication',False)
                or self.tasks.pending or self.migration_pending
                or not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified):return False
        try:raw=source_truth.capture(lib,titles=w.titles,artists=w.overrides,aliases=w.aliases,settings=w.settings)
        except (ValueError,TypeError) as exc:w._flash_status(str(exc),12);return False
        identity=uuid4().hex;key='inputs:'+identity
        descriptor=dict(format=_BUILDING,id=identity,scope=source_truth.scope(lib),input_key=key,
                        inputs_sha256=hashlib.sha256(raw).hexdigest(),edits={},error='')
        before_descriptor=w.settings.get(source_truth.MIGRATION);before_targets=w.settings.get(source_truth.TARGETS)
        w.settings[source_truth.MIGRATION]=descriptor
        w.settings[source_truth.TARGETS]={key:dict(format=_BUILDING,payload=json.loads(raw))}
        if not w._save_state():
            for name,value in ((source_truth.MIGRATION,before_descriptor),(source_truth.TARGETS,before_targets)):
                if value is None:w.settings.pop(name,None)
                else:w.settings[name]=value
            return False
        self.status(BACKGROUND_PAUSED_STATUS if self.background_paused else
            'Preparing frozen SoundBox metadata');self._resume_source_truth_build();return True

    def _resume_source_truth_build(self):
        w=self.window;descriptor=w.settings.get(source_truth.MIGRATION)
        if (self.closing or w._state_maintenance or getattr(w,'_state_publication',False) or self.tasks.pending
                or not descriptor or descriptor.get('format')!=_BUILDING or descriptor.get('error')
                or self.background_paused):return
        try:
            if not w.lib or source_truth.scope(w.lib)!=descriptor['scope']:return
            record=w.settings[source_truth.TARGETS][descriptor['input_key']]
            raw=source_truth._encode(record['payload'])
            if record['format']!=_BUILDING or hashlib.sha256(raw).hexdigest()!=descriptor['inputs_sha256']:
                raise ValueError('Frozen metadata inputs changed')
        except (ValueError,TypeError,KeyError) as exc:
            w.settings[source_truth.MIGRATION]={**descriptor,'error':str(exc)};w._save_state();self.status(str(exc),attention=True);return
        captured_id=descriptor['id'];captured_sha=descriptor['inputs_sha256'];captured_scope=deepcopy(descriptor['scope'])
        captured_origins=deepcopy(w.settings.get(alias_intents.ORIGINS,{}))
        def build():
            data=json.loads(raw);descriptor,targets=source_truth.build(raw,migration_id=captured_id)
            origins=alias_intents.origins(data,captured_origins,{})
            for identity,row in targets.items():
                if identity in origins:
                    row['curation']={field:provenance.snapshot(origins[identity],field,
                        data['titles' if field=='title' else 'artists'],data['info'].get(row['local_id'],{}).get(field))
                        for field in ('title','artist')}
            return descriptor,targets,origins
        worker=OneShotTask(build,log_label='Frozen native metadata plan')
        def completed(task):
            current=w.settings.get(source_truth.MIGRATION)
            if (self.closing or w._shutting_down or getattr(w,'_state_publication',False)
                    or not isinstance(current,dict) or current.get('format')!=_BUILDING
                    or current.get('id')!=captured_id or current.get('inputs_sha256')!=captured_sha
                    or current.get('scope')!=captured_scope):return
            if self.background_paused:self.status(BACKGROUND_PAUSED_STATUS);return
            if self._migration_error():self.status(self._migration_error(),attention=True);return
            if isinstance(task.result,Exception):
                w.settings[source_truth.MIGRATION]={**current,'error':str(task.result)};w._save_state();self.status(str(task.result),attention=True);return
            descriptor,targets,origins=task.result
            for row in targets.values():
                edits=current.get('edits',{}).get(row['local_id'])
                if edits and (edits['uuid'],edits['path'])==(row['uuid'],row['path']):
                    row.update(title=edits['title'],artist=edits['artist'],display_artist=edits['display_artist'])
                    row['fields']=list(dict.fromkeys([*row['fields'],*edits['fields']]))
            previous_targets=w.settings[source_truth.TARGETS]
            previous_origins=w.settings.get(alias_intents.ORIGINS)
            w.settings[source_truth.MIGRATION]=descriptor;w.settings[source_truth.TARGETS]=targets
            w.settings[alias_intents.ORIGINS]=alias_intents.merge_origins(origins,captured_origins,
                w.settings.get(alias_intents.ORIGINS,{}))
            if not w._save_state():
                w.settings[source_truth.MIGRATION]={**current,'error':'Frozen metadata plan could not be saved'}
                w.settings[source_truth.TARGETS]=previous_targets
                if previous_origins is None:w.settings.pop(alias_intents.ORIGINS,None)
                else:w.settings[alias_intents.ORIGINS]=previous_origins
                self.status('Frozen metadata plan needs storage attention',attention=True);return
            unresolved=len(descriptor['unresolved'])
            self.status('Frozen SoundBox metadata pending' if descriptor['order'] else
                f'No available native changes; {unresolved} unresolved saved entries preserved' if unresolved else
                'Native metadata already matches SoundBox',attention=bool(unresolved))
            self.schedule()
        self.tasks.adopt(worker,completed);worker.start()

    def display_fields(self,track):
        """Show a frozen pair until both fields acknowledge, then newer edits."""
        w=self.window;result={};descriptor=w.settings.get(source_truth.MIGRATION)
        targets=w.settings.get(source_truth.TARGETS,{})
        if self.queue_error() or self._migration_error():return result
        if (descriptor and descriptor.get('format')!=_BUILDING and w.lib
                and descriptor.get('scope')==source_truth.scope(w.lib)):
            key=(id(targets),getattr(targets,'revision',None),descriptor.get('id'))
            if self._truth_index is None or self._truth_index[0]!=key:
                self._truth_index=(key,{targets[value]['local_id']:(index,targets[value])
                    for index,value in enumerate(descriptor.get('order',[])) if value in targets
                    and isinstance(targets[value],dict) and isinstance(targets[value].get('local_id'),str)})
                self._truth_index_owner=targets
            value=self._truth_index[1].get(track.track_id)
            if value and value[0]>=descriptor.get('cursor',0):
                row=value[1]
                if (all(type(row.get(field)) is str for field in ('native_id','uuid','path','title','display_artist'))
                        and (row['native_id'],row['uuid'],row['path'])==(track.native_id,track.source_uuid,track.source_native_path or str(track.path))):
                    result=dict(title=row['title'],artist=row['display_artist'])
        if w.lib:
            for job in self.pending:
                if ((job['source'],job['library_id'],job['local_id'])==(str(w.lib.source),w.lib.source_library_id,track.track_id)
                        and _matches_track(job,track)):
                    result[job['field']]=job['value'] or (metadata.UNKNOWN_ARTIST if job['field']=='artist' else '')
        next_plan=w.settings.get(alias_intents.NEXT)
        if next_plan and next_plan['state']=='ready' and w.lib and next_plan['scope']==source_truth.scope(w.lib):
            identity=alias_intents.key(next_plan['scope'],dict(local_id=track.track_id,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            row=w.settings[alias_intents.TARGETS].get(identity)
            if row:result=dict(title=row['title'],artist=row['display_artist'])
        elif next_plan and next_plan['state']=='building' and w.lib and next_plan['scope']==source_truth.scope(w.lib):
            identity=alias_intents.key(next_plan['scope'],dict(local_id=track.track_id,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            raw=w.settings[alias_intents.TARGETS]['inputs:'+next_plan['id']]['raw']
            row=next_plan['edits'].get(identity) or raw['visible'].get(identity)
            if row:result['title']=row['title']
        return result

    def change_alias(self,source,target):
        """Save an alias gesture and its complete frozen successor together."""
        return self.change_aliases([] if target is None else [(source,target)],[source] if target is None else [])

    def change_title_policy(self,changes):
        """Persist one existing cleanup-policy gesture with its native plan."""
        w=self.window;reason=self.reason()
        if reason:w._flash_status(reason,12);return False
        if (not isinstance(changes,dict) or not changes or set(changes)-{'title_rules_off','title_words'}
                or any(not isinstance(value,list) or any(type(item) is not str or len(item)>4096 or '\0' in item
                    for item in value) for value in changes.values())):
            w._flash_status('Invalid title cleanup policy',12);return False
        changes=dict(changes)
        if 'title_rules_off' in changes:changes['title_rules_off']=metadata.normalize_title_rules_off(changes['title_rules_off'])
        if all(w.settings.get(field,[])==value for field,value in changes.items()):return True
        return self._stage_generation(w.aliases,w.overrides,policy_changes=changes)

    def change_aliases(self,accepts,unmerges):
        """One durable batch: unmerge first, then apply accepted mappings."""
        w=self.window;reason=self.reason()
        if reason:w._flash_status(reason,12);return False
        def valid(value):return type(value) is str and bool(value.strip()) and '\0' not in value and len(value)<=4096
        if (not isinstance(accepts,(list,tuple)) or not isinstance(unmerges,(list,tuple))
                or len(accepts)+len(unmerges)>source_truth._MAX_TRACKS
                or any(not valid(source) for source in unmerges)
                or any(not isinstance(pair,(list,tuple)) or len(pair)!=2 or not all(valid(value) for value in pair)
                       for pair in accepts)):
            w._flash_status('Invalid artist alias',12);return False
        aliases=dict(w.aliases)
        for source in unmerges:aliases.pop(source,None)
        for source,target in accepts:aliases[source]=target.strip()
        if aliases==w.aliases:return True
        return self._stage_generation(aliases,w.overrides)

    def change_artists(self,accepts,expected_pins):
        """Atomically retain accepted existing suggestions, never run a provider."""
        w=self.window;reason=self.reason()
        if reason:w._flash_status(reason,12);return False
        try:
            if (not isinstance(accepts,(list,tuple)) or len(accepts)>source_truth._MAX_TRACKS
                    or any(not isinstance(pair,(list,tuple)) or len(pair)!=2
                        or any(type(value) is not str or not value.strip() or '\0' in value or len(value)>4096 for value in pair)
                        for pair in accepts)):
                raise ValueError('Invalid accepted artist changes')
            identities=[pair[0] for pair in accepts]
            if len(set(identities))!=len(identities):raise ValueError('Ambiguous accepted artist changes')
            if (not isinstance(expected_pins,dict) or set(expected_pins)!={'scope','tracks'}
                    or not isinstance(expected_pins['tracks'],dict)
                    or set(identities)-set(expected_pins['tracks'])):
                raise ValueError('Artist selection was not captured before the dialog')
            fresh=alias_intents.artist_pins(w.lib,identities)
            if (fresh['scope']!=expected_pins['scope'] or any(
                    fresh['tracks'][identity]!=expected_pins['tracks'][identity] for identity in identities)):
                raise ValueError('The artist selection changed; reopen suggestions')
        except (ValueError,KeyError,TypeError) as exc:w._flash_status(str(exc),12);return False
        artists=dict(w.overrides)
        for identity,value in accepts:artists[identity]=value.strip()
        info=w.settings.get('track_info',{})
        has_artist_overlay=any('artist' in info.get(identity,{}) for identity in identities)
        if artists==w.overrides and not has_artist_overlay:return True
        updated_info=deepcopy(info) if has_artist_overlay else None
        if updated_info is not None:
            for identity in identities:
                row=updated_info.get(identity)
                if row is None:continue
                row.pop('artist',None)
                if not row:updated_info.pop(identity)
        return self._stage_generation(w.aliases,artists,info=updated_info,changed_artists=identities)

    def _stage_generation(self,aliases,artists,*,info=None,changed_artists=None,policy_changes=None):
        """One successor owns a global artist policy change and its preimage."""
        w=self.window
        scope=source_truth.scope(w.lib);visible={};owned={};owned_titles={};previous={}
        for track in w.lib.tracks_by_id.values():
            identity=alias_intents.key(scope,dict(local_id=track.track_id,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            fields=self.display_fields(track)
            row=w._row_for(track) if hasattr(w,'_row_for') else engine.track_row(track,w.overrides,w.titles,
                registry=w.lib.artist_registry,junk=w._title_junk())
            row.update({field:value for field,value in w.settings.get('track_info',{}).get(track.track_id,{}).items()
                        if field in ('title','artist')})
            visible[identity]=dict(title=fields.get('title',row['title']),artist=fields.get('artist',row['artist']))
            owned[identity]=[job['value'] for job in self.pending if job['field']=='artist'
                and (job['source'],job['library_id'],job['local_id'])==(scope['source'],scope['library_id'],track.track_id)
                and _matches_track(job,track)]
            owned_titles[identity]=[job['value'] for job in self.pending if job['field']=='title'
                and (job['source'],job['library_id'],job['local_id'])==(scope['source'],scope['library_id'],track.track_id)
                and _matches_track(job,track)]
        present=set(visible)
        for name in (source_truth.MIGRATION,alias_intents.NEXT):
            prior=w.settings.get(name)
            if not prior or prior['scope']!=scope:continue
            targets=w.settings.get(source_truth.TARGETS if name==source_truth.MIGRATION else alias_intents.TARGETS,{})
            if prior.get('format')==_BUILDING:
                payload=alias_intents.missing_inputs(targets[prior['input_key']]['payload'],present)
                if payload:previous['source-inputs:'+prior['id']]=dict(source_raw=payload)
            elif name==alias_intents.NEXT and prior['state']=='building':
                raw=targets['inputs:'+prior['id']]['raw']
                previous.update(deepcopy(raw['previous']))
                payload=alias_intents.missing_inputs(raw['payload'],present)
                if payload:
                    identities={alias_intents.key(scope,track) for track in payload['tracks']}
                    filtered={**raw,'payload':payload,'previous':{},
                        **{key:{identity:row for identity,row in raw[key].items() if identity in identities}
                           for key in ('origins','visible','owned','owned_titles')}}
                    previous['alias-inputs:'+prior['id']]=dict(alias_raw=filtered)
            else:
                plan=prior if name==source_truth.MIGRATION else prior['plan']
                cursor=plan['cursor']
                if plan.get('active') and not plan['active']['operations'] and not plan.get('error'):cursor+=1
                unfinished=set(plan['unresolved'])|set(plan['order'][cursor:])
                previous.update({identity:deepcopy(row) for identity,row in targets.items()
                                 if identity in unfinished and identity not in present})
        settings=w.settings if info is None else {**w.settings,'track_info':info}
        if policy_changes is not None:settings={**settings,**policy_changes}
        if changed_artists:
            origins=dict(settings.get(alias_intents.ORIGINS,{}))
            for track in w.lib.tracks_by_id.values():
                if track.track_id not in changed_artists:continue
                identity=alias_intents.key(scope,dict(local_id=track.track_id,native_id=track.native_id,
                    uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
                origin=origins.get(identity)
                if origin is None:
                    fields=visible[identity]
                    automatic=metadata.effective_artist(track.track_id,track.name,track.artist,
                        registry=w.lib.artist_registry,stem=track.path.stem)
                    origin=dict(scope=scope,local_id=track.track_id,native_id=track.native_id,uuid=track.source_uuid,
                        path=track.source_native_path or str(track.path),automatic=automatic,native_artist=track.artist,
                        automatic_title=fields['title'],native_title=track.name,title_shield='')
                    origin=provenance.seed(origin,title=track.name,artist=track.artist,display_artist=fields['artist'])
                origins[identity]=provenance.edit(origin,('artist',))
            settings={**settings,alias_intents.ORIGINS:origins}
        try:raw=alias_intents.capture(w.lib,titles=w.titles,artists=artists,aliases=aliases,
            settings=settings,visible=visible,owned=owned,owned_titles=owned_titles,previous=previous,
            origin_artists=w.overrides,changed_artists=changed_artists,policy=policy_changes is not None)
        except (ValueError,KeyError,TypeError) as exc:w._flash_status(str(exc),12);return False
        identity=uuid4().hex;before_settings=w.settings;before_aliases=w.aliases;before_artists=w.overrides
        w.settings={**w.settings,alias_intents.NEXT:dict(format=alias_intents.FORMAT,id=identity,scope=scope,
            state='building',inputs_sha256=hashlib.sha256(raw).hexdigest(),edits={},error=''),
            alias_intents.TARGETS:{'inputs:'+identity:dict(raw=json.loads(raw))},
            alias_intents.ORIGINS:json.loads(raw)['origins']}
        if info is not None:w.settings['track_info']=info
        if policy_changes is not None:w.settings.update(policy_changes)
        w.aliases=aliases;w.overrides=artists
        if not w._save_state():
            w.settings=before_settings;w.aliases=before_aliases;w.overrides=before_artists;return False
        w._build_artist_registry();self.status('Artist updates pending');self.schedule();return True

    def _resume_alias_build(self):
        w=self.window;next_plan=w.settings.get(alias_intents.NEXT)
        if (not next_plan or next_plan['state']!='building' or next_plan['error'] or self.tasks.pending
                or self.closing or w._shutting_down or w._state_maintenance or getattr(w,'_state_publication',False)
                or self.background_paused):return
        if not w.lib or source_truth.scope(w.lib)!=next_plan['scope']:return
        captured=deepcopy(next_plan);raw=source_truth._encode(w.settings[alias_intents.TARGETS]['inputs:'+captured['id']]['raw'])
        worker=OneShotTask(lambda:alias_intents.build(raw,migration_id=captured['id']),log_label='Frozen native artist alias plan')
        def completed(task):
            current=w.settings.get(alias_intents.NEXT)
            if not current or current['id']!=captured['id']:
                self.schedule();return
            if self.closing or w._shutting_down or getattr(w,'_state_publication',False):return
            if not w.lib or source_truth.scope(w.lib)!=captured['scope']:return
            if self.background_paused:self.status(BACKGROUND_PAUSED_STATUS);return
            if self._migration_error():self.status(self._migration_error(),attention=True);return
            if isinstance(task.result,Exception):
                w.settings[alias_intents.NEXT]={**current,'error':str(task.result)};w._save_state();self.status(str(task.result),attention=True);return
            descriptor,targets,origins=task.result
            alias_intents.apply_edits(descriptor,targets,current['edits'])
            before=w.settings
            captured_origins=json.loads(raw)['origins']
            merged_origins=alias_intents.merge_origins(origins,captured_origins,before.get(alias_intents.ORIGINS,{}))
            explicit_titles={row['local_id'] for row in current['edits'].values() if 'title' in row['fields']}
            titles,merged_origins=alias_intents.protect_titles(targets,w.titles,merged_origins,
                current_bindings=alias_intents.title_bindings(w.lib),junk=w._title_junk(),explicit_titles=explicit_titles)
            before_titles=w.titles
            w.settings={**before,alias_intents.NEXT:{**current,'state':'ready','plan':descriptor},
                        alias_intents.TARGETS:targets,alias_intents.ORIGINS:merged_origins}
            w.titles=titles
            if not w._save_state():
                w.settings={**before,alias_intents.NEXT:{**current,'error':'Artist alias plan could not be saved'}}
                w.titles=before_titles
                self.status('Artist alias plan needs storage attention',attention=True);return
            w._invalidate_rows()
            reload=getattr(w,'_reload_current_view',None)
            if reload:reload()
            self.schedule()
        self.tasks.adopt(worker,completed);worker.start()

    def _promote_alias(self):
        w=self.window;next_plan=w.settings.get(alias_intents.NEXT)
        if not next_plan or next_plan['state']!='ready' or self.pending or self.background_paused:return False
        if not w.lib or source_truth.scope(w.lib)!=next_plan['scope']:return False
        current=w.settings.get(source_truth.MIGRATION)
        if current and current.get('active') and current['active']['operations']:return False
        descriptor,targets=alias_intents.prune(next_plan['plan'],w.settings[alias_intents.TARGETS])
        before=w.settings
        w.settings={**before,source_truth.MIGRATION:descriptor,source_truth.TARGETS:targets}
        w.settings.pop(alias_intents.NEXT,None);w.settings.pop(alias_intents.TARGETS,None)
        if not w._save_state():w.settings=before;self.status('Artist alias promotion needs storage attention',attention=True);return False
        self.schedule();return True

    def _source_truth_step(self):
        w=self.window;descriptor=w.settings.get(source_truth.MIGRATION)
        if w._state_maintenance or getattr(w,'_state_publication',False):return False
        if self._background_state_busy():self.timer.start(2000);return True
        if descriptor is None:return False
        if not self._ensure_truth_validation():return True
        descriptor=w.settings.get(source_truth.MIGRATION)
        error=self._migration_error(force_truth=True)
        if error:self.status(error,attention=True);return True
        if descriptor.get('format')==_BUILDING:
            if self.background_paused:self.status(BACKGROUND_PAUSED_STATUS)
            else:self._resume_source_truth_build()
            return True
        if not self.migration_pending:return False
        if descriptor.get('error'):self.status(descriptor['error'],attention=True);return True
        targets=w.settings.get(source_truth.TARGETS,{})
        try:
            if not w.lib or source_truth.scope(w.lib)!=descriptor['scope']:return True
            if descriptor['active'] is not None:
                if descriptor['active']['operations']:
                    raise ValueError('Frozen metadata pair acknowledgements are incomplete; intentions are preserved')
                if not self.tags.verified:return True
                target=targets[descriptor['active']['target']]
                matches=[track for track in w.lib.tracks_by_id.values() if (track.native_id,track.source_uuid,
                    track.source_native_path or str(track.path))==(target['native_id'],target['uuid'],target['path'])]
                if len(matches)!=1 or (matches[0].name,matches[0].artist)!=(target['title'],target['artist']):
                    raise ValueError('The title or artist differs from SoundBox. Retry to save the preserved values.')
                before=w.settings;descriptor={**descriptor,'cursor':descriptor['cursor']+1,'active':None}
                w.settings={**before,source_truth.MIGRATION:descriptor}
                def advanced():
                    if self.background_paused:self._show_paused_status()
                    elif descriptor['cursor']==len(descriptor['order']):
                        unresolved=len(descriptor.get('unresolved',[]))
                        self.status(f'Available native metadata saved; {unresolved} unresolved saved entries preserved'
                                    if unresolved else 'Frozen SoundBox metadata saved',attention=bool(unresolved))
                    self.schedule()
                self._commit_background_state({'settings':before},
                    {('settings',source_truth.MIGRATION,'cursor'):descriptor['cursor'],
                     ('settings',source_truth.MIGRATION,'active'):None},committed=advanced,
                    failed=lambda:self.status('Source metadata progress could not be saved',attention=True),
                    message='Source metadata progress could not be saved')
                return True
            if descriptor['cursor']==len(descriptor['order']):
                self.status(f"{len(descriptor['unresolved'])} unresolved saved entries preserved; native identities need attention",attention=True)
                return True
            if self.background_paused:self.status(BACKGROUND_PAUSED_STATUS);return True
            target_key=descriptor['order'][descriptor['cursor']];target=targets[target_key]
            if target['info']:raise ValueError('Frozen Track Info fields need their separate native save profile')
            queue=[];operations={}
            for field in target['fields']:
                _intent_value(target[field],field);identity=uuid4().hex;operations[field]=identity
                queue.append(dict(id=identity,source=descriptor['scope']['source'],library_id=descriptor['scope']['library_id'],
                    local_id=target['local_id'],native_id=target['native_id'],track_uuid=target['uuid'],path=target['path'],
                    field=field,value=target[field],request=None,retry=False,error=''))
                if target.get('curation',{}).get(field) is not None:
                    queue[-1]['curation']=target['curation'][field]
            if not queue:raise ValueError('Frozen metadata target has no supported changes')
            before=w.settings;active=dict(target=target_key,operations=operations)
            w.settings={**before,source_truth.MIGRATION:{**descriptor,'active':active},KEY:queue}
            self._commit_background_state({'settings':before},
                {('settings',source_truth.MIGRATION,'active'):active,('settings',KEY):queue},
                committed=self.schedule,failed=lambda:self.status('Frozen metadata pair could not be queued',attention=True),
                message='Frozen metadata pair could not be queued')
            return True
        except (ValueError,KeyError,TypeError) as exc:
            w.settings[source_truth.MIGRATION]={**descriptor,'error':str(exc)};w._save_state();self.status(str(exc),attention=True);return True

    @property
    def pending(self):
        value=self.window.settings.get(KEY,[])
        return value if isinstance(value,list) else []

    def queue_error(self):
        if self._state_commit_error:return self._state_commit_error
        if self._retirement_error():return self._retirement_error()
        try:
            queue=self.window.settings.get(KEY,[])
            if not isinstance(queue,list) or len(queue)>256:raise ValueError('Queue')
            seen=set()
            for job in queue:
                if (not isinstance(job,dict) or not _JOB<=set(job) or set(job)-_JOB-{'companion_conflict','curation'}
                        or not native._operation(job['id']) or job['id'] in seen
                        or any(type(job[k]) is not str for k in _JOB-{'request','retry'})
                        or not Path(job['source']).is_absolute() or not job['library_id'] or not job['local_id']
                        or not Path(job['path']).is_absolute() or type(job['retry']) is not bool
                        or bool(job['native_id'])!=bool(job['track_uuid'])):raise ValueError('Identity')
                if job['native_id']:native._native_id(job['native_id'])
                if job['field'] not in ('title','artist'):raise ValueError('Field')
                _intent_value(job['value'],job['field'])
                if 'curation' in job:provenance.validate_snapshot(job['curation'])
                if 'companion_conflict' in job:
                    proof=job['companion_conflict']
                    if (not isinstance(proof,dict) or set(proof)!={'field','value','accepted'}
                            or proof['field'] not in {'title','artist'}-{job['field']}
                            or type(proof['accepted']) is not bool):raise ValueError('Companion proof')
                    _intent_value(proof['value'],proof['field'])
                if job['request'] is not None:
                    request=native.validate_request(job['request'])
                    if any(request[k]!=job[j] for k,j in (('operation_id','id'),('library_id','library_id'),
                        ('track_id','native_id'),('track_uuid','track_uuid'),('path','path'),('field','field'),('value','value'))):
                        raise ValueError('Captured request differs from intention')
                seen.add(job['id'])
        except (ValueError,KeyError,TypeError,WriteProofError):return 'Saved metadata edits need attention; intentions are preserved'
        return self._migration_error()

    def can_quit_with_unadmitted_metadata(self):
        """Preserve disabled intentions on disk without claiming publication.

        A native request or active worker still uses the ordinary drain gate.
        Unstarted read-only derivation is reconstructed on the next verified
        source read; durable curation and frozen publication intentions remain.
        A scoped pause can also retain an idle frozen backlog. Explicit queued
        edits and a pair already admitted to the FIFO must still drain normally.
        """
        if self.queue_error():return False
        if (self._state_commit_pending or self.tasks.pending or self.tags.busy
                or self.window.settings.get(ACKS)
                or any(job.get('request') is not None for job in self.pending)):
            return False
        if self.background_paused:
            return bool(not self.pending and not self._paused_progress_pending()
                and self._paused_status()==BACKGROUND_PAUSED_STATUS)
        if self.factory:return False
        return self.reason()==EDITING_DISABLED_REASON

    def reason(self):
        w=self.window;lib=w.lib
        if self.queue_error():return self.queue_error()
        if self._state_commit_pending:return 'Saving song updates…'
        if self.closing or w._shutting_down or w._state_maintenance or getattr(w,'_state_publication',False):
            return 'Library operation in progress'
        if not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified:return 'Refresh the native library before editing metadata'
        scope=source_truth.scope(lib)
        if any((job['source'],job['library_id'])!=(scope['source'],scope['library_id']) for job in self.pending):
            return 'Switch to the original library to finish its metadata saves'
        for key in (source_truth.MIGRATION,alias_intents.NEXT):
            plan=w.settings.get(key)
            if plan and plan['scope']!=scope and (key==alias_intents.NEXT or plan.get('format')==_BUILDING
                    or plan.get('active') or plan.get('unresolved') or plan.get('cursor',0)<len(plan.get('order',[]))):
                return 'Switch to the original library to finish its metadata saves'
        if not self.factory and native.load_authority(self.tags.data_root/'rekordbox-metadata-authority.json',
                lib.source,lib.source_library_id) is None:return EDITING_DISABLED_REASON
        return ''

    def _store(self,lib):
        return self.factory(lib) if self.factory else native.SourceMetadataStore(native.load_authority(
            self.tags.data_root/'rekordbox-metadata-authority.json',lib.source,lib.source_library_id),self.tags.data_root)

    def _readback(self,previous):
        if self.loader is not None:return self.loader(Path(previous.source))
        reader=getattr(self.window,'native_reader',None)
        if reader is not None:return reader.read_metadata(previous)
        if self.factory is None:
            raise RuntimeError('The background native reader is unavailable; restart SoundBox before retrying metadata saves')
        from soundbox import rekordbox_db
        return rekordbox_db.load_metadata_readback(previous)

    def enqueue(self,track_id,changes):
        """Persist intended effective values and local curation atomically."""
        w=self.window;reason=self.reason()
        if reason:w._flash_status(reason,12);return False
        if (not isinstance(changes,dict) or not changes or set(changes)-{'title','artist'}):
            w._flash_status('Those metadata fields need their separate native save profile',12);return False
        track=w.lib.tracks_by_id.get(track_id)
        if track is None or track_id in getattr(w.lib,'source_binding_issues',{}):
            w._flash_status('Refresh the song identity before editing metadata',12);return False
        previous=engine.track_row(track,w.overrides,w.titles,registry=w.lib.artist_registry,junk=w._title_junk())
        previous.update({field:value for field,value in w.settings.get('track_info',{}).get(track_id,{}).items()
                         if field in ('title','artist')})
        previous.update(self.display_fields(track))
        scope=source_truth.scope(w.lib)
        origin_identity=alias_intents.key(scope,dict(local_id=track_id,native_id=track.native_id,
            uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
        existing_origins=w.settings.get(alias_intents.ORIGINS,{})
        relocated_origins=(existing_origins if origin_identity in existing_origins else
                           alias_intents.library_origins(w.lib,existing_origins))
        prior_origin=relocated_origins.get(origin_identity)
        owned=[job['value'] for job in self.pending if job['field']=='artist' and job['local_id']==track_id
               and (job['source'],job['library_id'])==(scope['source'],scope['library_id']) and _matches_track(job,track)]
        if prior_origin and track.artist in [prior_origin['native_artist'],*owned]:automatic=prior_origin['automatic']
        else:
            bases=[metadata.derive_artist(t.name,t.artist)[0] for t in w.lib.tracks_by_id.values()]
            trusted=[t.artist for t in w.lib.tracks_by_id.values() if t.artist]+list(w.overrides.values())
            automatic=metadata.effective_artist(track_id,w.titles.get(track_id) or track.name,track.artist,
                registry=metadata.build_registry(bases,trusted),stem=track.path.stem)
        owned_titles=[job['value'] for job in self.pending if job['field']=='title' and job['local_id']==track_id
               and (job['source'],job['library_id'])==(scope['source'],scope['library_id']) and _matches_track(job,track)]
        automatic_title=(prior_origin['automatic_title'] if prior_origin and track.name in
            [prior_origin['native_title'],*owned_titles] else metadata.effective_title(track_id,track.name,previous['artist'],junk=w._title_junk()))
        if prior_origin and 'provenance' in prior_origin and track.name in [prior_origin['native_title'],*owned_titles]:
            automatic_title=metadata.tidy_title(prior_origin['provenance']['title_base'],w._title_junk())
        origin=dict(scope=scope,local_id=track_id,native_id=track.native_id,uuid=track.source_uuid,
                    path=track.source_native_path or str(track.path),automatic=automatic,native_artist=track.artist,
                    automatic_title=automatic_title,native_title=track.name,
                    title_shield=prior_origin.get('title_shield','') if prior_origin else '')
        if prior_origin and 'provenance' in prior_origin:origin['provenance']=prior_origin['provenance']
        elif prior_origin is None:
            origin=provenance.seed(origin,title=track.name,artist=track.artist,display_artist=previous['artist'])
        origin=provenance.edit(origin,changes)
        titles,artists=dict(w.titles),dict(w.overrides)
        for field,value in changes.items():
            if type(value) is not str:w._flash_status('Metadata must be text',12);return False
            values=artists if field=='artist' else titles
            if value.strip():values[track_id]=value.strip()
            else:values.pop(track_id,None)
        registry=w.lib.artist_registry
        if 'artist' in changes:
            bases=[metadata.derive_artist(t.name,t.artist)[0] for t in w.lib.tracks_by_id.values()]
            trusted=[t.artist for t in w.lib.tracks_by_id.values() if t.artist]+[v for v in artists.values() if v.strip()]
            registry=metadata.build_registry(bases,trusted,w.aliases)
        effective=engine.track_row(track,artists,titles,registry=registry,junk=w._title_junk())
        if 'artist' in changes and not changes['artist'].strip():
            effective['artist']=registry.canonical(origin['automatic']) if registry else origin['automatic']
            effective['title']=metadata.effective_title(track_id,track.name,effective['artist'],titles,w._title_junk())
        if 'title' in changes and not changes['title'].strip():effective['title']=origin['automatic_title']
        info=dict(w.settings.get('track_info',{}).get(track_id,{}))
        for field in ('title','artist'):
            if field not in changes and field in info:effective[field]=info[field]
        retained=self.display_fields(track)
        for field in ('title','artist'):
            if field not in changes and field in retained:effective[field]=retained[field]
        if 'artist' not in changes:effective['artist']=previous['artist']
        if 'title' not in changes:effective['title']=previous['title']
        display_artist=effective['artist']
        if effective['artist']==metadata.UNKNOWN_ARTIST:effective['artist']=''
        try:
            for field in ('title','artist'):_intent_value(effective[field],field)
        except ValueError as exc:w._flash_status(str(exc),12);return False
        shield_row={**origin,'title':effective['title'],'artist':effective['artist'],'display_artist':display_artist}
        titles,protected_origins=alias_intents.protect_titles({origin_identity:shield_row},titles,{origin_identity:origin},
            current_bindings=alias_intents.title_bindings(w.lib,allow_drafts=True),junk=w._title_junk(),
            explicit_titles={track_id} if 'title' in changes and changes['title'].strip() else ())
        origin=protected_origins[origin_identity]
        wanted=[]
        for field,original in (('title',track.name),('artist',track.artist)):
            queued=[job for job in self.pending if (job['source'],job['library_id'],job['local_id'],job['field'])==
                    (str(w.lib.source),w.lib.source_library_id,track_id,field) and _matches_track(job,track)]
            if (queued[-1]['value'] if queued else original)!=effective[field] or field in changes and not queued:
                wanted.append(field)
        if len(self.pending)+len(wanted)>256:w._flash_status('Wait for pending metadata saves to finish',12);return False
        before_settings=w.settings;before_titles=w.titles;before_artists=w.overrides
        w.settings=dict(before_settings)
        queue=list(self.pending)
        for field in wanted:
            queue.append(dict(id=uuid4().hex,source=str(w.lib.source),library_id=w.lib.source_library_id,
                local_id=track_id,native_id=track.native_id if track.source_uuid else '',track_uuid=track.source_uuid,
                path=getattr(track,'source_native_path','') or str(track.path),field=field,value=effective[field],
                request=None,retry=False,error=''))
            captured=provenance.snapshot(origin,field,titles if field=='title' else artists,
                info.get(field) if field not in changes else None)
            if captured is not None:queue[-1]['curation']=captured
        w.settings[KEY]=queue;w.titles=titles;w.overrides=artists
        w.settings[alias_intents.ORIGINS]={**relocated_origins,origin_identity:origin}
        if set(info)&set(changes):
            extras=dict(w.settings.get('track_info',{}));row=dict(info)
            for field in changes:row.pop(field,None)
            if row:extras[track_id]=row
            else:extras.pop(track_id,None)
            w.settings['track_info']=extras
        descriptor=w.settings.get(source_truth.MIGRATION)
        if descriptor and descriptor.get('scope')==source_truth.scope(w.lib):
            fields=list(dict.fromkeys([*changes,*wanted]))
            if descriptor.get('format')==_BUILDING:
                edits=dict(descriptor.get('edits',{}));edits[track_id]=dict(uuid=track.source_uuid,
                    path=track.source_native_path or str(track.path),title=effective['title'],artist=effective['artist'],
                    display_artist=display_artist,fields=fields)
                w.settings[source_truth.MIGRATION]={**descriptor,'edits':edits}
            else:
                targets=dict(w.settings.get(source_truth.TARGETS,{}))
                for key,row in targets.items():
                    if (row['local_id'],row['uuid'],row['path'])==(track_id,track.source_uuid,track.source_native_path or str(track.path)):
                        targets[key]={**row,'title':effective['title'],'artist':effective['artist'],'display_artist':display_artist,
                                      'fields':list(dict.fromkeys([*row['fields'],*fields]))}
                w.settings[source_truth.TARGETS]=targets
        next_plan=w.settings.get(alias_intents.NEXT)
        if next_plan and next_plan['scope']==source_truth.scope(w.lib):
            identity=alias_intents.key(next_plan['scope'],dict(local_id=track_id,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            edit=dict(scope=next_plan['scope'],local_id=track_id,native_id=track.native_id,uuid=track.source_uuid,
                path=track.source_native_path or str(track.path),title=effective['title'],artist=effective['artist'],
                display_artist=display_artist,fields=list(changes))
            if next_plan['state']=='building':
                previous_edit=next_plan['edits'].get(identity,{})
                edit['fields']=list(dict.fromkeys([*previous_edit.get('fields',[]),*edit['fields']]))
                w.settings[alias_intents.NEXT]={**next_plan,'edits':{**next_plan['edits'],identity:edit}}
            else:
                targets=deepcopy(w.settings[alias_intents.TARGETS])
                alias_intents.apply_edits(next_plan['plan'],targets,{identity:edit})
                w.settings[alias_intents.TARGETS]=targets
        if not w._save_state():
            w.titles=before_titles;w.overrides=before_artists;w.settings=before_settings
            return False
        if 'artist' in changes:w._build_artist_registry()
        else:w._invalidate_rows((track_id,))
        reload=getattr(w,'_reload_current_view',None)
        if reload:QTimer.singleShot(0,w,reload)
        self.status('Metadata saves pending' if queue else '');self.schedule();return True

    def status(self,message,*,attention=False):
        previous=self._attention_message
        # A paused scheduler may synchronously run from changed.emit(). That
        # scheduling preference cannot dismiss a saved-state failure notice.
        if attention:self._attention_message=message
        elif message!=BACKGROUND_PAUSED_STATUS:self._attention_message=''
        if getattr(self.tags,'metadata_update_status','')!=message:
            self.tags.metadata_update_status=message;self.tags.changed.emit()
        if attention and message and message!=previous:
            getattr(self.window,'_background_notice',self.window._flash_status)(str(message),12)

    def schedule(self):
        if self.closing or self.window._shutting_down or self._state_commit_pending:return
        if self._retirement_ack is not None and not self._background_state_busy():
            self.timer.start(0);return
        if self.background_paused and not self.pending and not self.window.settings.get(ACKS) and not self._paused_progress_pending():
            self._show_paused_status();return
        if self.pending or self.migration_pending or self.window.settings.get(ACKS):
            # A released source lane supersedes its old contention poll. Only a
            # measured failure/native-owner deadline may postpone the next try.
            delay=max(0,math.ceil((self.not_before-time.monotonic())*1000))
            if not self.timer.isActive() or self.timer.remainingTime()>delay:
                self.timer.start(delay)

    def _retains(self,job):
        return next((i for i,current in enumerate(self.pending) if isinstance(current,dict)
                     and {**current,'error':''}=={**job,'error':''}),None)

    def fail(self,job,message,*,companion=None):
        self.timer.stop();index=self._retains(job)
        if index is not None:
            queue=list(self.pending);queue[index]={**queue[index],'error':str(message)}
            if companion is not None:queue[index]['companion_conflict']=companion
            self.window.settings[KEY]=queue;self.window._save_state()
            self.status('Metadata save needs attention — '+str(message),attention=True)
        else:
            getattr(self.window,'_background_notice',self.window._flash_status)(str(message),12)

    def _expected_companion(self,job):
        w=self.window;field='artist' if job['field']=='title' else 'title'
        scope=dict(source=job['source'],kind='rekordbox-db',library_id=job['library_id'])
        identity=alias_intents.key(scope,dict(local_id=job['local_id'],native_id=job['native_id'],
            uuid=job['track_uuid'],path=job['path']))
        for name,records in ((alias_intents.NEXT,alias_intents.TARGETS),(source_truth.MIGRATION,source_truth.TARGETS)):
            plan=w.settings.get(name)
            if plan and plan.get('scope')==scope and plan.get('state')!='building' and plan.get('format')!=_BUILDING:
                if name==source_truth.MIGRATION and identity not in plan['order'][plan['cursor']:]:continue
                row=w.settings.get(records,{}).get(identity)
                if row and not row['error']:return field,row[field]
        origin=w.settings.get(alias_intents.ORIGINS,{}).get(identity)
        if origin is None:
            proof=job.get('companion_conflict')
            return (field,proof['value']) if proof else None
        if field=='title':return field,w.titles.get(job['local_id'],origin['native_title'])
        value=w.overrides.get(job['local_id'])
        if value is None:return field,origin['native_artist']
        registry=w.lib.artist_registry if w.lib else None
        value=registry.canonical(value) if registry else value
        return field,'' if value==metadata.UNKNOWN_ARTIST else value

    def _future_companion(self,job,field,track):
        if any(row['field']==field and (row['source'],row['library_id'],row['local_id'])==
               (job['source'],job['library_id'],job['local_id']) and _matches_track(row,track) for row in self.pending[1:]):
            return True
        next_plan=self.window.settings.get(alias_intents.NEXT)
        if next_plan and next_plan['state']=='ready':
            for row in self.window.settings[alias_intents.TARGETS].values():
                if (row['scope']['source'],row['scope']['library_id'],row['local_id'],row['native_id'],row['uuid'],row['path'])==(
                        job['source'],job['library_id'],job['local_id'],job['native_id'],job['track_uuid'],job['path']):
                    return not row['error'] and field in row['fields']
        return False

    def _background_migration_wait(self):
        """Yield unstarted frozen-backlog work while the user is browsing.

        Captured requests, committed ACKs and explicit edits keep their ordinary
        completion path; input never cancels a source operation in flight.
        """
        delay=getattr(self.window,'_background_library_not_before',0)-time.monotonic()
        if delay<=0:return False
        descriptor=self.window.settings.get(source_truth.MIGRATION)
        if not descriptor:return False
        if self.pending:
            head=self.pending[0]
            if (head['request'] is not None or head['id'] not in
                    (descriptor.get('active') or {}).get('operations',{}).values()):return False
        elif not self.migration_pending:return False
        self.timer.start(max(1,int(delay*1000)+1));return True

    def pump(self):
        w=self.window
        if self.closing or w._shutting_down or self._state_commit_pending:return
        if self._state_commit_error:self.status(self._state_commit_error,attention=True);return
        if w._state_maintenance or getattr(w,'_state_publication',False):
            if self.pending or self.migration_pending or w.settings.get(ACKS):self.timer.start(2000)
            return
        if self._background_state_busy():
            if self.pending or self.migration_pending or w.settings.get(ACKS):self.timer.start(2000)
            return
        if self._retirement_error():self.status(self._retirement_error(),attention=True);return
        if w.settings.get(ACKS):
            error=w.settings[ACKS][0]['error']
            if error:
                self.status('Metadata receipt retirement needs attention — '+error,attention=True);return
            self._resume_receipt_retirement();return
        descriptor=w.settings.get(source_truth.MIGRATION)
        if self._migration_error():self.status(self._migration_error(),attention=True);return
        if self.queue_error():self.status(self.queue_error(),attention=True);return
        if self.background_paused and not self.pending:
            if self._paused_progress_pending():self._source_truth_step()
            self._show_paused_status();return
        if self._background_migration_wait():return
        if not self._ensure_truth_validation():return
        next_plan=w.settings.get(alias_intents.NEXT)
        if next_plan and next_plan['error']:
            self.status(next_plan['error'],attention=True);return
        if next_plan and next_plan['state']=='building':self._resume_alias_build();return
        if next_plan and not self.pending:
            self._promote_alias();return
        if descriptor and descriptor.get('format')==_BUILDING:self._resume_source_truth_build();return
        if not self.pending:
            if self._resume_derived():return
            self._source_truth_step();return
        if self.pending[0]['error']:
            self.status('Metadata save needs attention — '+self.pending[0]['error'],attention=True);return
        if (self.tags.source_job_owned or self.tags.prep_priority or w._refresh_worker is not None
                or w._state_maintenance or device_blocks_library_work(w)):self.timer.start(2000);return
        imports=getattr(w,'shared_import_controller',None)
        if imports and imports.source_priority:self.timer.start(2000);return
        reason=self.reason()
        if reason:self.status(reason,attention=True);return
        if time.monotonic()<self.not_before:self.schedule();return
        job=deepcopy(self.pending[0]);lib=w.lib
        bindings_owner=w.settings.get(shared_imports.BINDINGS)
        captured_bindings=deepcopy(bindings_owner) if bindings_owner is not None else None
        if (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id']):
            self.status('Switch to the original library to finish its metadata saves',attention=True);return
        if job['request'] is None and any((pending.get('source'),pending.get('library_id'))==(job['source'],job['library_id'])
                and pending.get(field)==job['local_id'] and not pending.get('error') and pending.get('state')!='failed'
                for key,field in (('analysis_jobs','track_id'),('native_analysis_jobs','local_id'))
                for pending in w.settings.get(key,[])):
            publisher=getattr(w,'native_analysis_controller',None)
            if publisher:publisher.schedule()
            self.timer.start(2000);return
        if not job['track_uuid']:
            track=lib.tracks_by_id.get(job['local_id'])
            if track is not None and (str(track.path)!=job['path']
                    or job['local_id'] in getattr(lib,'source_binding_issues',{})):
                self.fail(job,'The imported song identity changed; its original metadata intention is preserved');return
            if not track or not track.source_uuid:
                if imports:imports.schedule()
                self.timer.start(2000);return
            before_settings=w.settings;w.settings=dict(before_settings)
            before=w.settings[KEY];queue=list(before)
            queue[0]={**queue[0],'native_id':track.native_id,'track_uuid':track.source_uuid,
                      'path':getattr(track,'source_native_path','') or str(track.path)}
            w.settings[KEY]=queue
            origins=w.settings.get(alias_intents.ORIGINS,{})
            matches=[row for row in origins.values() if row['scope']==source_truth.scope(lib)
                     and row['local_id']==job['local_id'] and not row['uuid'] and row['path']==job['path']]
            replacements={('settings',KEY):queue}
            if len(matches)==1:
                row={**matches[0],'native_id':track.native_id,'uuid':track.source_uuid,
                     'path':track.source_native_path or str(track.path),'native_artist':track.artist,'native_title':track.name}
                identity=alias_intents.key(row['scope'],row)
                w.settings[alias_intents.ORIGINS]={**origins,identity:row}
                replacements[('settings',alias_intents.ORIGINS,identity)]=row
            self._commit_background_state({'settings':before_settings},replacements,
                committed=self.schedule,failed=lambda:self.fail(job,'The registered metadata binding could not be saved'),
                message='The registered metadata binding could not be saved; retry after fixing storage')
            return
        observed_truth=self._observed_truth()
        truth_context=None
        if observed_truth is not None:
            current,truth_targets=observed_truth
            truth_context=(current,{**current,'active':deepcopy(current.get('active'))},truth_targets,
                           self._truth_stamp(current,truth_targets),self._truth_epoch(),self._truth_source_context())
        def work():
            if truth_context is not None:
                owner,current,truth_targets,stamp,epoch,source_context=truth_context
                context=(epoch,source_context)
                def same_domain():
                    return (self._truth_epoch()==epoch and self._truth_source_context()==source_context
                        and w.settings.get(source_truth.MIGRATION) is owner
                        and w.settings.get(source_truth.TARGETS) is truth_targets
                        and owner.get('cursor')==current.get('cursor') and owner.get('active')==current.get('active')
                        and self._truth_stamp(owner,truth_targets)==stamp)
                checked=self._snapshot_truth(current,truth_targets,stamp,
                    proof=self._truth_static_proof,context=context) if same_domain() else None
                if checked is None or not same_domain():
                    raise WriteProofError('Saved metadata intentions changed before writing; retry to verify the preserved values')
                frozen,detached=checked
                try:verification=epoch[0].verify_selected_values(self._truth_saved_values(frozen,detached))
                except VerificationChanged:return dict(validation_retry=True)
                if not epoch[0].current_verification(verification):return dict(validation_retry=True)
                if not same_domain():
                    raise WriteProofError('Saved metadata intentions changed before writing; retry to verify the preserved values')
                self._truth_static_proof=self._static_truth_identity(frozen,detached,context)
                if frozen['scope']!=dict(source=job['source'],kind='rekordbox-db',library_id=job['library_id']):
                    raise WriteProofError('Saved metadata intentions belong to another source library')
                active=frozen.get('active')
                if active and active['operations'].get(job['field'])==job['id']:
                    selected=detached[active['target']]
                    if (selected['local_id'],selected['native_id'],selected['uuid'],selected['path'])!=(
                            job['local_id'],job['native_id'],job['track_uuid'],job['path']):
                        raise WriteProofError('The selected metadata intention differs from its frozen song identity')
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib);store=self._store(lib)
            if job['request'] is None:
                return dict(request=store.capture_request(job['native_id'],job['track_uuid'],job['path'],
                    job['field'],job['value'],operation_id=job['id']))
            if job['retry']:return dict(request=store.retry_request(job['request']))
            store.apply(job['request'])
            retirement=getattr(store,'retirement_entry',None)
            library=self._readback(lib)
            if captured_bindings is not None:library=shared_imports.project(library,captured_bindings,unavailable={})
            if job['local_id'] in library.source_binding_issues:
                raise WriteProofError('The selected song registration needs reconciliation; its metadata intention is preserved')
            selected=None;match_count=0
            def indexed_rows():
                nonlocal selected,match_count
                for local,track in library.tracks_by_id.items():
                    if (track.native_id,track.source_uuid)==(job['native_id'],job['track_uuid']):
                        path=track.source_native_path or str(track.path)
                        if path==job['path']:
                            match_count+=1
                            if selected is None:selected=(local,track.native_id,track.source_uuid,path,track.name,track.artist)
                    yield local,track
            pin_index=source_truth.prepare_pin_index(library,rows=indexed_rows())
            return dict(library=library,selected=selected,match_count=match_count,
                        curation_pin_index=pin_index,
                        retirement=retirement(job['request']) if retirement else None,
                        projected_bindings_owner=bindings_owner,
                        projected_scope=(str(lib.source),lib.source_library_id))
        def completed(worker):
            if worker.error:self.fail(job,worker.error);return
            if worker.result.get('validation_retry'):
                self.not_before=time.monotonic()+.1;self.status('Checking saved song updates…');self.schedule();return
            if worker.result.get('waiting'):
                self.not_before=time.monotonic()+2;self.status('Metadata pending — quit rekordbox to save',attention=True);self.schedule();return
            if self._retains(job)!=0:return
            if self.queue_error():self.fail(job,self.queue_error());return
            if not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id']):
                self.fail(job,'Saved in the original library; switch back to finish metadata');return
            if worker.result.get('projected_bindings_owner') is not None and (
                    w.settings.get(shared_imports.BINDINGS) is not worker.result['projected_bindings_owner']
                    or worker.result.get('projected_scope')!=(str(w.lib.source),w.lib.source_library_id)):
                self.fail(job,'Song registration identities changed while saving; retry to verify the saved metadata');return
            before=w.settings[KEY];queue=list(before)
            if 'request' in worker.result:
                queue[0]={**queue[0],'request':worker.result['request'],'retry':False}
                prior=w.settings;w.settings={**prior,KEY:queue}
                self._commit_background_state({'settings':prior},{('settings',KEY):queue},
                    committed=self.schedule,failed=lambda:self.fail(job,'The exact metadata request could not be saved'),
                    message='The exact metadata request could not be saved; retry after fixing storage')
                return
            library=worker.result['library']
            selected=worker.result.get('selected');matches=[]
            if isinstance(selected,tuple) and len(selected)==6 and worker.result.get('match_count')==1:
                track=library.tracks_by_id.get(selected[0])
                if track is not None and (track.native_id,track.source_uuid,track.source_native_path or str(track.path),
                        track.name,track.artist)==selected[1:]:matches=[track]
            if ((str(library.source),library.source_library_id)!=(job['source'],job['library_id'])
                    or library.source_kind!='rekordbox-db' or len(matches)!=1
                    or getattr(matches[0],'name' if job['field']=='title' else 'artist')!=job['value']):
                self.fail(job,'Native metadata readback could not be verified; its intention is preserved');return
            companion=self._expected_companion(job)
            future=(companion is not None and self._future_companion(job,companion[0],matches[0]))
            consent=job.get('companion_conflict',{}).get('accepted',False)
            if companion and getattr(matches[0],'name' if companion[0]=='title' else 'artist')!=companion[1] and not future and not consent:
                self.fail(job,'The saved '+companion[0]+' differs from rekordbox. Retry to save the preserved SoundBox title and artist.',
                    companion=dict(field=companion[0],value=companion[1],accepted=False));return
            repair=None
            if (consent and companion and not future and
                    getattr(matches[0],'name' if companion[0]=='title' else 'artist')!=companion[1]):
                repair={**job,'id':uuid4().hex,'field':companion[0],'value':companion[1],
                        'request':None,'retry':False,'error':''}
                repair.pop('curation',None)
                repair['companion_conflict']=dict(field=job['field'],value=job['value'],accepted=False)
                reference=dict(scope=source_truth.scope(library),local_id=job['local_id'],native_id=job['native_id'],
                               uuid=job['track_uuid'],path=job['path'])
                repair_origin=w.settings.get(alias_intents.ORIGINS,{}).get(alias_intents.key(reference['scope'],reference))
                if repair_origin:
                    captured=provenance.snapshot(repair_origin,repair['field'],w.titles if repair['field']=='title' else w.overrides,
                        w.settings.get('track_info',{}).get(job['local_id'],{}).get(repair['field']))
                    if captured is not None:repair['curation']=captured
            prior_descriptor=w.settings.get(source_truth.MIGRATION)
            prior_acks=w.settings.get(ACKS)
            prior_origins=w.settings.get(alias_intents.ORIGINS)
            prior_origin_validation=self._validated_origins;prior_alias_validation=self._alias_validation
            next_owner=w.settings.get(alias_intents.NEXT);targets_owner=w.settings.get(alias_intents.TARGETS)
            prior_titles=w.titles;prior_artists=w.overrides
            prior_info=w.settings.get('track_info')
            retirement=worker.result.get('retirement')
            if retirement is not None:
                try:native.validate_retirement(retirement)
                except WriteProofError as exc:self.fail(job,str(exc));return
                if len(w.settings.get(ACKS,[]))>=256:self.fail(job,'Metadata receipt retirement queue is full');return
            prior_settings=w.settings;w.settings=dict(prior_settings);origin_identity=None
            if retirement is not None:
                w.settings[ACKS]=[*w.settings.get(ACKS,[]),dict(entry=retirement,error='')]
            if prior_origins:
                identity=alias_intents.key(source_truth.scope(library),dict(local_id=job['local_id'],
                    native_id=job['native_id'],uuid=job['track_uuid'],path=job['path']))
                if identity in prior_origins:
                    origin_identity=identity
                    origin=provenance.acknowledge({**prior_origins[identity],
                        'native_artist' if job['field']=='artist' else 'native_title':job['value']},job)
                    info,retired_overlay=provenance.retire_overlay(origin,job,w.settings.get('track_info',{}))
                    if retired_overlay:w.settings['track_info']=info
                    remaining=([repair] if repair else [])+queue[1:]
                    owned=[(row['field'],row['value']) for row in remaining
                        if row['local_id']==job['local_id'] and (row['source'],row['library_id'])==
                        (job['source'],job['library_id']) and _matches_track(row,matches[0])]
                    origin,w.titles,w.overrides=provenance.reconcile(origin,title=matches[0].name,
                        artist=matches[0].artist,titles=w.titles,artists=w.overrides,owned=owned,junk=w._title_junk(),
                        fields=(job['field'],),retired_overlay=retired_overlay)
                    alias_intents.validate_origins({identity:origin})
                    w.settings[alias_intents.ORIGINS]={**prior_origins,
                        identity:origin}
            if prior_descriptor and prior_descriptor.get('format')!=_BUILDING and prior_descriptor.get('active'):
                active=prior_descriptor['active'];operations=active['operations']
                if operations.get(job['field'])==job['id']:
                    operations={field:identity for field,identity in operations.items() if identity!=job['id']}
                    if repair and active['target']==alias_intents.key(prior_descriptor['scope'],dict(local_id=job['local_id'],
                            native_id=job['native_id'],uuid=job['track_uuid'],path=job['path'])):
                        operations[repair['field']]=repair['id']
                    w.settings[source_truth.MIGRATION]={**prior_descriptor,'active':{**active,'operations':operations}}
            w.settings[KEY]=([repair] if repair else [])+queue[1:]
            replacements={('settings',KEY):w.settings[KEY]};removals=[];missing=object()
            if w.settings.get(ACKS) is not prior_acks:replacements[('settings',ACKS)]=w.settings[ACKS]
            if w.settings.get(source_truth.MIGRATION) is not prior_descriptor:
                replacements[('settings',source_truth.MIGRATION,'active','operations')]=w.settings[source_truth.MIGRATION]['active']['operations']
            if origin_identity is not None:
                replacements[('settings',alias_intents.ORIGINS,origin_identity)]=w.settings[alias_intents.ORIGINS][origin_identity]
            local=job['local_id']
            for key,old,current in (('title_overrides',prior_titles,w.titles),
                                    ('artist_overrides',prior_artists,w.overrides),
                                    ('track_info',prior_info or {},w.settings.get('track_info',{}))):
                previous_value=old.get(local,missing);value=current.get(local,missing)
                if value==previous_value:continue
                path=('settings',key,local) if key=='track_info' else (key,local)
                if value is missing:removals.append(path)
                else:replacements[path]=value
            try:self._stage_ack_source_curation(library,replacements,removals,
                                                pin_index=worker.result.pop('curation_pin_index',None))
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                w.settings=prior_settings;w.titles=prior_titles;w.overrides=prior_artists
                self.fail(job,'Metadata saved in rekordbox; saved curation needs reconciliation: '+str(exc));return
            def acknowledged():
                projected=worker.result.get('projected_bindings_owner')
                scope=worker.result.get('projected_scope')
                if projected is not None and (w.settings.get(shared_imports.BINDINGS) is not projected
                        or not w.lib or scope!=(str(w.lib.source),w.lib.source_library_id)):
                    self._state_commit_error='Saved metadata needs a fresh song-registration check before continuing'
                    self.status(self._state_commit_error,attention=True)
                    reconcile=getattr(w,'_native_reconcile_source',None)
                    if callable(reconcile):reconcile()
                    return
                acknowledged_origins=w.settings.get(alias_intents.ORIGINS)
                if (prior_origins is prior_origin_validation and acknowledged_origins is not prior_origins
                        and prior_alias_validation==(id(prior_origins),id(next_owner),id(targets_owner))
                        and w.settings.get(alias_intents.NEXT) is next_owner
                        and w.settings.get(alias_intents.TARGETS) is targets_owner):
                    self._validated_origins=acknowledged_origins
                    self._alias_validation=(id(acknowledged_origins),id(next_owner),id(targets_owner))
                self.not_before=0;self.status('Metadata saves pending' if self.pending else
                    BACKGROUND_PAUSED_STATUS if self.background_paused else '')
                if not self.closing and not w._shutting_down:
                    if projected is not None:
                        w._publish_metadata_library(library,(job['local_id'],),
                            projected_bindings_owner=projected,projected_scope=scope)
                    else:w._publish_metadata_library(library,(job['local_id'],))
                self.schedule()
            self._commit_background_state({'settings':prior_settings,'titles':prior_titles,'overrides':prior_artists},
                replacements,removals=removals,committed=acknowledged,
                failed=lambda:self.fail(job,'Metadata saved in rekordbox; local acknowledgement failed. Retry after fixing storage.'),
                message='Metadata saved in rekordbox; local acknowledgement needs storage attention. Retry to finish.')
        if not self.tags._run_source_job('metadata',work,completed):self.timer.start(2000)

    def retry(self):
        if (self._state_commit_pending or self.closing or self.tags.busy or getattr(self.tags,'checking',False)
                or getattr(getattr(self.tags,'owner',None),'pending',0)):return False
        if self._truth_observed_error is not None:
            if self.window._state_maintenance or getattr(self.window,'_state_publication',False):return False
            self._truth_observed_error=None;self._truth_error_owners=();self._truth_error_epoch=None
            self._truth_error_progress=None;self._truth_observed_proof=None
            self._ensure_truth_validation();return True
        if self._state_commit_error:
            previous=self._state_commit_error;self._state_commit_error=''
            if self.queue_error():self._state_commit_error=previous;return False
            self.status('Metadata saves pending');self.schedule();return True
        if (self.closing or self.tags.busy or getattr(self.tags,'checking',False)
                or getattr(getattr(self.tags,'owner',None),'pending',0) or self.queue_error()):return False
        if self.window.settings.get(ACKS):
            before=self.window.settings[ACKS]
            self.window.settings[ACKS]=[{**before[0],'error':''},*before[1:]]
            if not self.window._save_state():self.window.settings[ACKS]=before;return False
            self.schedule();return True
        next_plan=self.window.settings.get(alias_intents.NEXT)
        if next_plan and next_plan['error']:
            self.window.settings[alias_intents.NEXT]={**next_plan,'error':''}
            if not self.window._save_state():self.window.settings[alias_intents.NEXT]=next_plan;return False
            self.schedule();return True
        if not self.pending:
            if next_plan:
                self.schedule();return True
            descriptor=self.window.settings.get(source_truth.MIGRATION)
            if not descriptor:return False
            targets=self.window.settings.get(source_truth.TARGETS)
            updated={**descriptor,'error':''}
            if descriptor.get('active') and not descriptor['active']['operations']:
                identity=descriptor['active']['target'];updated['active']=None
                self.window.settings[source_truth.TARGETS]={**targets,identity:{**targets[identity],'fields':['title','artist']}}
            self.window.settings[source_truth.MIGRATION]=updated
            if not self.window._save_state():
                self.window.settings[source_truth.MIGRATION]=descriptor
                if targets is not None:self.window.settings[source_truth.TARGETS]=targets
                return False
            self.schedule();return True
        before=self.window.settings[KEY];queue=list(before);queue[0]={**queue[0],'error':'','retry':True}
        if 'companion_conflict' in queue[0]:
            job=queue[0];scope=dict(source=job['source'],kind='rekordbox-db',library_id=job['library_id'])
            identity=alias_intents.key(scope,dict(local_id=job['local_id'],native_id=job['native_id'],uuid=job['track_uuid'],path=job['path']))
            if identity not in alias_intents.title_bindings(self.window.lib):
                self.window._flash_status('The song identity changed; its original metadata pair is preserved',12);return False
            companion=self._expected_companion(job)
            if companion is None:return False
            queue[0]['companion_conflict']=dict(field=companion[0],value=companion[1],accepted=True)
        self.window.settings[KEY]=queue
        if not self.window._save_state():self.window.settings[KEY]=before;return False
        self.not_before=0;self.schedule();return True

    def stop(self):self.closing=True;self.timer.stop();self.tasks.drain()

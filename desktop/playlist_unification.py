"""One explicitly requested migration, serialized through accepted source writers."""
from copy import deepcopy
import time

from PySide6.QtCore import QObject,QTimer

from soundbox import engine, playlist_unification as merge, shared_imports
from soundbox.rekordbox_playlist_store import request_for,batch_for,MAX_BATCH_COMMANDS

_BATCH_TRACK_LIMIT=16384


class PlaylistUnificationController(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.closing=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(50)
        self.timer.timeout.connect(self.pump)
        window.tag_controller.idle.connect(self.schedule)
        window.tag_controller.changed.connect(self.schedule)

    def state(self,*,strict=False):
        value=self.window.settings.get(merge.KEY)
        if value is None:return None
        if value is not getattr(self,'_validated',None):
            try:merge.validate(value)
            except (ValueError,TypeError,KeyError):
                if strict:raise
                return None
            self._validated=value
        return value if merge.matches(value,self.window.lib) else None

    def export_aliases(self):
        value=self.state()
        if value is not getattr(self,'_alias_state',None):
            self._alias_state=value
            self._aliases=({node['id']:key[7:] for key,node in value['nodes'].items()
                           if key.startswith('custom:')} if value and value['customs_done'] else {})
        return getattr(self,'_aliases',{})

    def schedule(self,*_args):
        value=self.state()
        if (not self.closing and self.window.settings.get(merge.KEY) is not None
                and not (value and value['customs_done'])):self.timer.start(50)

    def save(self,value):
        w=self.window;old=w.settings.get(merge.KEY)
        w.settings[merge.KEY]=value
        if w._save_state():return True
        w.settings[merge.KEY]=old
        return False

    def fail(self,error):
        value=deepcopy(self.state())
        if value:
            value['error']=str(error);self.save(value)
        self.window._flash_status('Playlist merge needs attention — '+str(error),20)

    def pump(self):
        w=self.window
        if (self.closing or w._shutting_down or w._state_maintenance or not w.lib
                or not w.tag_controller.verified or w.lib.source_kind!='rekordbox-db'):return
        try:value=self.state(strict=True)
        except (ValueError,TypeError,KeyError) as exc:
            w._flash_status(str(exc),20);return
        # Artist organization is a virtual view. Completed legacy migration is
        # terminal; future imports use their explicit ordinary import gestures.
        # No clean library/source selection creates migration state or jobs.
        if value is None or value['customs_done'] or value['error']:return
        if time.monotonic()<getattr(w,'_background_library_not_before',0):
            self.timer.start(250);return
        if (w.tag_controller.owned or w._refresh_worker is not None or w._device_operation_owned()
                or w.playlist_controller.pending or w.shared_import_controller.pending
                or w.settings.get('pending_prep_edits') or w.native_analysis_controller.pending):
            self.timer.start(500);return
        if w.playlist_controller.reason():
            if value:self.timer.start(2000)
            return
        # Register the existing visible SoundBox-only songs first, keeping their
        # logical IDs, analysis and curation. Each admitted import has its normal
        # exact persisted request/receipts before any source mutation.
        tracks=w._visible_collection_tracks()
        missing=[t for t in tracks if t.imported and not t.source_uuid]
        if missing:
            pending=shared_imports.admit(w.shared_import_controller.pending,w.lib,[missing[0]],
                                        dict(kind='collection'),{'automatic':False,'bpm':False,'key':False,'first_cue':False})
            old=w.settings.get(shared_imports.KEY,[]);w.settings[shared_imports.KEY]=pending
            if not w._save_state():w.settings[shared_imports.KEY]=old;return
            w.shared_import_controller.schedule();self.schedule();return
        specs=merge.specifications(w.custom,w.settings.get('custom_playlist_tree'))
        # One bounded durable envelope composes existing create/append/remove
        # commands. Future IDs are bound with the queue before source admission;
        # a failure/restart never adopts a native row merely by matching name.
        catalog=deepcopy(w.lib.source_playlist_catalog)
        nodes={r['ID']:r for r in catalog['nodes'] if r['rb_local_deleted']==0}
        memberships={p.pid:list(p.keys) for p in w.lib.playlists}
        proposed=deepcopy(value);commands=[];selected_count=0
        for spec in specs:
            if len(commands)>=MAX_BATCH_COMMANDS or selected_count>=_BATCH_TRACK_LIMIT:break
            binding=proposed['nodes'].get(spec['key'])
            parent=proposed['nodes'][spec['parent']]['id'] if spec['parent'] else 'root'
            if binding is None:
                # Existing siblings are never adopted by matching name alone.
                if any(r['ParentID']==parent and r['Name']==spec['name'] for r in nodes.values()):
                    self.fail('An existing native item conflicts with “'+spec['name']+'”; both originals are preserved');return
                request=request_for(catalog,'create',name=spec['name'],parent=parent,folder=spec['folder'])
                commands.append(request)
                binding=dict(id=request['playlist_id'],uuid=request['playlist_uuid'])
                proposed['nodes'][spec['key']]=binding
                node=dict(ID=binding['id'],UUID=binding['uuid'],Name=spec['name'],ParentID=parent,
                          Seq=1+max((r['Seq'] for r in nodes.values() if r['ParentID']==parent),default=0),
                          Attribute=int(spec['folder']),rb_local_deleted=0)
                nodes[node['ID']]=node;catalog['nodes'].append(node);memberships[node['ID']]=[]
                if len(commands)>=MAX_BATCH_COMMANDS:break
            node=nodes.get(binding['id'])
            if node is None or node['UUID']!=binding['uuid']:
                self.fail('A merged native playlist was removed or replaced; refresh before continuing');return
            if node['ParentID']!=parent or node['Attribute']!=int(spec['folder']) or node['Name']!=spec['name']:
                self.fail('A merged native folder changed; original hierarchy is preserved');return
            if spec['folder']:continue
            actual=memberships.get(binding['id'])
            if actual is None:self.fail('Merged playlist readback is unavailable');return
            wanted=[tid for tid in spec['ids'] if tid not in w.hidden]
            if any(tid not in w.lib.tracks_by_id for tid in wanted):
                self.fail('A playlist contains an unresolved song identity; its membership is preserved');return
            if any(not w.lib.tracks_by_id[tid].source_uuid for tid in wanted):
                self.fail('A playlist song has no verified native registration');return
            if actual!=wanted[:len(actual)]:
                self.fail('The original playlist order changed during its merge; both versions are preserved');return
            missing=wanted[len(actual):]
            if missing:
                commands.append(request_for(catalog,'append',identity=binding['id'],
                    tracks=[w.lib.tracks_by_id[tid] for tid in missing[:_BATCH_TRACK_LIMIT-selected_count]],allow_duplicate=True))
                selected_count+=len(commands[-1]['tracks'])
        if commands:
            merge.validate(proposed)
            request=batch_for(w.lib.source_playlist_catalog,commands)
            before=w.settings.get(merge.KEY);w.settings[merge.KEY]=proposed
            if not w.playlist_controller.enqueue(request):w.settings[merge.KEY]=before
            self.schedule();return
        # Every legacy custom node/membership is now verified. This one-time
        # operation ends permanently; Artists never needs a native playlist.
        updated=deepcopy(value);updated['customs_done']=True
        if self.save(updated):
            w._apply_settings_to_lib(invalidate_rows=False)
            w._populate_tree(engine.summary(w.lib))
            if (getattr(w,'_view_key','') or '').startswith('sb:'):
                if w.stack.currentIndex() == 1:
                    w._view_key='sb:Collection'
                else:
                    w.sb_tree.setCurrentItem(w.sb_tree.topLevelItem(0))
                    w._show_collection();w._restore_view('sb:Collection')

    def stop(self):
        self.closing=True;self.timer.stop()

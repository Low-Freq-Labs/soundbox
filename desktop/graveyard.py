"""On-demand read-only Graveyard information and storage accounting."""
from copy import deepcopy
import json
import os
import stat

from PySide6.QtCore import QObject,QTimer,Signal

from soundbox import graveyard,shared_imports
from soundbox.state_io import atomic_bytes
from .task_owner import TaskOwner
from .workers import OneShotTask
from .devices import human


_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024


def _retain_archive(area, relative, document):
    """Skip an identical history publication after a fresh locked byte read."""
    payload = json.dumps(document, allow_nan=False).encode()
    if len(payload) > _MAX_ARCHIVE_BYTES:
        raise ValueError('Graveyard identity archive exceeds its bound')
    with area.lock:
        path = area.path(str(relative))
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            before = None
        else:
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_ARCHIVE_BYTES:
                    raise ValueError('Graveyard identity archive is invalid')
                before = stream.read(_MAX_ARCHIVE_BYTES + 1)
            if len(before) > _MAX_ARCHIVE_BYTES:
                raise ValueError('Graveyard identity archive exceeds its bound')
        if before != payload:
            atomic_bytes(path, payload)


class GraveyardController(QObject):
    changed=Signal()

    def __init__(self,window):
        super().__init__(window);self.window=window;self.tasks=TaskOwner(self)
        self.entries={};self.closing=False;self.again=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(300)
        self.timer.timeout.connect(self.scan)

    def ids(self):
        w=self.window
        return (set(getattr(w,'hidden',set()))|{tid for bag in getattr(w,'removals',{}).values() for tid in bag})-set(w.settings.get('graveyard_disposals',{}))

    def schedule(self,*_args):
        w=self.window;grave=getattr(w,'_grave',None)
        if self.closing or grave is None or not grave.isVisible():return
        self.again=True
        if not self.timer.isActive():self.timer.start()

    def scan(self):
        w=self.window
        if self.closing or w._shutting_down or not w.lib:return
        if self.tasks.pending or w.tag_controller.owned or w._refresh_worker is not None:
            self.timer.start();return
        if not w._grave.isVisible():return
        ids=self.ids();source=w.lib.source if w.lib.source_kind=='rekordbox-db' else None
        library_id=w.lib.source_library_id;imported=deepcopy(w.imported)
        bindings=deepcopy(w.settings.get(shared_imports.BINDINGS,[]));self.again=False
        self.status('Checking storage…')
        def work():
            remembered={};path=graveyard.archive_path(w.tag_controller.data_root,source,library_id)
            if path.exists():
                if path.is_symlink() or path.stat().st_size>4*1024*1024:raise ValueError('Graveyard identity archive is invalid')
                data=json.loads(path.read_bytes())
                if (data.get('format')!=graveyard.RECOVERED_FORMAT or data.get('source')!=str(source)
                        or data.get('library_id')!=library_id):raise ValueError('Graveyard identity archive belongs to another library')
                remembered=data['tracks']
            return dict(entries=graveyard.catalog(source,library_id,ids,imported=imported,bindings=bindings,
                remembered=remembered,should_cancel=worker.cancellation.is_set),remembered=remembered)
        worker=OneShotTask(work,log_label='Graveyard storage')
        def retired(job):
            if self.closing or w._shutting_down:return
            if (not w.lib or w.lib.source_library_id!=library_id
                    or (w.lib.source if w.lib.source_kind=='rekordbox-db' else None)!=source or self.ids()!=ids):
                self.schedule();return
            if isinstance(job.result,Exception):self.status('Storage unavailable — '+str(job.result));return
            self.entries=job.result['entries']
            archive_error=''
            if source is not None and w._state_repository.writable and not w._state_maintenance:
                remembered=job.result['remembered']
                for tid,entry in self.entries.items():
                    native=entry.get('native',{})
                    if native.get('uuid') and entry.get('display') and entry.get('path'):
                        remembered[tid]=dict(id=entry['native_id'],uuid=native['uuid'],path=entry['path'],
                            source=str(source),library_id=library_id,display=entry['display'],proof=entry.get('proof',{}))
                payload=dict(format=graveyard.RECOVERED_FORMAT,source=str(source),library_id=library_id,tracks=remembered)
                try:
                    _retain_archive(w._state_repository.area,
                        graveyard.archive_path(w.tag_controller.data_root,source,library_id)
                        .relative_to(w.tag_controller.data_root),payload)
                except (OSError,ValueError) as exc:
                    archive_error='Storage checked; could not retain historical file identities — '+str(exc)
            items=[w._grave.topLevelItem(index) for index in range(w._grave.topLevelItemCount())]
            for item in items:
                from .settings_page import _GRAVE_ROLE
                data=item.data(0,_GRAVE_ROLE);entry=self.entries.get(data[1],{}) if data else {}
                display=entry.get('display')
                if display:
                    title=w.titles.get(data[1]) or display['title']
                    if data[0]=='rem':
                        playlist=next((p.name for p in w.lib.playlists if p.pid==data[2]),'?')
                        title+=f'   (from {playlist})'
                    item.setText(0,title);item.setToolTip(0,entry.get('canonical') or entry.get('path') or '')
            w._filter_graveyard();self.selection_changed();self.changed.emit()
            if archive_error:self.status(archive_error)
            if self.again:self.schedule()
        self.tasks.adopt(worker,retired);worker.start()

    def info(self,tid):
        entry=self.entries.get(tid,{})
        row=deepcopy(entry.get('display'))
        if row:
            w=self.window;row['id']=tid
            row['title']=w.titles.get(tid) or row['title']
            row['artist']=w.overrides.get(tid) or row.get('artist','')
            row.update(w.settings.get('track_info',{}).get(tid,{}))
        return row

    def status(self,text):
        label=getattr(self.window,'_grave_storage',None)
        if label:label.setText(text)

    def selection_changed(self):
        from .settings_page import _GRAVE_ROLE
        ids=[data[1] for item in self.window._grave.selectedItems() if (data:=item.data(0,_GRAVE_ROLE))]
        total=graveyard.totals(self.entries);selected=graveyard.totals(self.entries,ids)
        text=f"Graveyard: {human(total['size'])} in {total['files']} files"
        if ids:text+=f"  ·  Selected: {human(selected['size'])}"
        if total['unresolved']:text+=f"  ·  {total['unresolved']} paths unresolved"
        if total['unavailable']:text+=f"  ·  {total['unavailable']} missing or unavailable"
        self.status(text)

    def shutdown(self):
        self.closing=True;self.timer.stop();self.tasks.drain()

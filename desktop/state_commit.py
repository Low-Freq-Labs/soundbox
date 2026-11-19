"""Ordered background canonical commits; GUI admission never means durability."""
import json
import logging
import threading
from collections import deque

from PySide6.QtCore import QObject, QThread, QTimer

from soundbox.state_repository import StatePatch, apply_state_patch
from soundbox.state_io import StateChangedError
from soundbox.operation_control import operation_scope,checkpoint
from .task_owner import TaskOwner

LOG=logging.getLogger('soundbox')


class StatePublicationChangedError(StateChangedError):
    """Canonical delta is durable, but newer GUI ownership prevents publication."""
    durable = True


_ROOTS={'settings':'settings','title_overrides':'titles','artist_overrides':'overrides',
        'artist_aliases':'aliases'}
PREFERENCE_KEYS=frozenset(('quantize','beat_jump_mode','waveform_zoom','autoplay_mode',
    'columns_state','columns_keys','sidebar_split','sidebar_width','window_geometry','window_rect','keyboard',
    'analysis','waveform_mode','beat_count_mode','custom_playlist_expanded','theme_accent',
    'theme_text_mode','theme_text_custom','device_views',
    'stock_ipod_identity','stock_ipod_models'))  # display history only, never writer/signing authority


class _CommitTask(QThread):
    """An admitted durable continuation finishes even during shutdown drain."""
    def __init__(self,repository,patch):
        super().__init__();self.repository=repository;self.patch=patch;self.result=None

    def run(self):
        try:self.result=self.repository.commit_patch(self.patch)
        except Exception as exc:self.result=exc


class _CheckpointTask(QThread):
    """Verify a selected immutable capture; never publish a canonical delta."""
    def __init__(self,repository,expected,missing,payload,validate):
        super().__init__();self.repository=repository;self.expected=expected;self.missing=missing
        self.payload=payload;self.validate=validate;self.result=None
        self.cancellation=threading.Event()

    def cancel(self):self.cancellation.set()

    def run(self):
        try:
            with operation_scope(self.cancellation):
                checkpoint()
                if self.validate is not None:
                    prepared=self.validate(self.payload)
                    if prepared is not None:self.payload=prepared
                checkpoint()
                self.result=self.repository.verify_selected_values(self.expected,missing=self.missing)
        except Exception as exc:self.result=exc


def apply_gui_patch(window,patch:StatePatch):
    """Merge committed leaves; unrelated navigation/preferences retain ownership."""
    replacements,removals=patch.edits()
    roots={key:getattr(window,attribute) for key,attribute in _ROOTS.items()}
    roots=apply_state_patch(roots,replacements,removals)
    for key,attribute in _ROOTS.items():setattr(window,attribute,roots[key])


class StateCommitter(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.owner=TaskOwner(self)
        self.token=None;self.deferred_save=False
        self._checkpoints=deque()
        self._preferences={};self._preference_epoch=None;self._preference_inflight=False
        self._preference_error=None;self._preference_yield=False
        self._preference_timer=QTimer(self);self._preference_timer.setSingleShot(True)
        self._preference_timer.timeout.connect(self._pump_preferences)
        self.owner.drained.connect(self._pump_preferences)
        self.owner.drained.connect(self._pump_checkpoints)
        self.window.tag_controller.idle.connect(self._schedule_preferences)
        self.window.tag_controller.idle.connect(self._pump_checkpoints)

    @property
    def pending(self):return self._active or self.preferences_pending or bool(self._checkpoints)

    @property
    def _active(self):return self.token is not None or bool(self.owner.pending)

    @property
    def preferences_pending(self):return bool(self._preferences) or self._preference_inflight

    @property
    def preferences_error(self):return self._preference_error

    def defer_save(self):
        # A failed synchronous curation call cannot become an asynchronous
        # success. Keep its dirty marker for an explicit durable full checkpoint;
        # automatic preference callers supply their exact leaves separately.
        self.deferred_save=True

    def _epoch(self):
        repository=self.window._state_repository
        return repository,repository.generation,repository.area.generation

    def queue_preferences(self,values,removals=()):
        """Admit selected UI preferences, never acknowledge canonical durability.

        Musical policies, source identity, queues and curation are excluded.
        Immutable small JSON leaves retain the latest value per key while source
        ACKs finish; no complete GUI document is captured or read on a worker.
        """
        removed=tuple(removals)
        if (not isinstance(values,dict) or set(values)&set(removed)
                or any(key not in PREFERENCE_KEYS for key in (*values,*removed))):
            raise ValueError('Asynchronous preference commits require exact UI preference keys')
        if (self.window._state_maintenance
                or self.owner.stopping and not self.window._shutting_down):return False
        encoded={key:json.dumps(value,allow_nan=False,separators=(',',':')).encode('utf-8')
                 for key,value in values.items()}
        epoch=self._epoch()
        if self._preference_epoch is not None and self._preference_epoch!=epoch:
            # Explicit restoration replaced that preference generation. Never
            # carry its old queued leaves into a new user gesture's generation.
            self._preferences={}
        self._preferences.update(encoded);self._preferences.update(dict.fromkeys(removed))
        if self._preferences:self._preference_epoch=epoch
        if encoded or removed:self._preference_error=None
        self._schedule_preferences()
        return True

    def preferences_saved(self):
        """A successful full synchronous checkpoint already contains these leaves."""
        if self._preference_inflight:return
        self._preference_timer.stop();self._preferences={};self._preference_epoch=None
        self._preference_error=None;self.deferred_save=False

    def retry_preferences(self):
        """An explicit user retry may reschedule only retained UI preferences."""
        w=self.window
        if (not self._preferences or self._preference_error is None or self.owner.stopping
                or w._shutting_down or w._state_maintenance):return False
        self._preference_error=None;self._schedule_preferences();return True

    def _schedule_preferences(self):
        w=self.window
        if (self._preferences and not self._preference_inflight and self._preference_error is None
                and not w._shutting_down and not w._state_maintenance and not self.owner.stopping):
            self._preference_timer.start(0)

    def _pump_preferences(self,*,allow_shutdown=False):
        w=self.window
        if self._checkpoints:
            self._pump_checkpoints();return False
        if (not self._preferences or self._preference_inflight or self._preference_error is not None
                or self._active or self.owner.stopping or w._state_maintenance
                or w._shutting_down and not allow_shutdown):return False
        if self._preference_yield and not allow_shutdown:
            self._preference_yield=False;self._schedule_preferences();return False
        if self._preference_epoch!=self._epoch():
            self._preference_error=StateChangedError('Library changed before queued preferences could be saved')
            return False
        batch=dict(self._preferences)
        replacements={('settings',key):json.loads(value) for key,value in batch.items() if value is not None}
        removals=[('settings',key) for key,value in batch.items() if value is None]
        self._preference_inflight=True
        def committed():
            for key,value in batch.items():
                if key in self._preferences and self._preferences[key]==value:self._preferences.pop(key)
            if not self._preferences:self._preference_epoch=None
            self._preference_error=None;self._preference_inflight=False;self._preference_yield=True
        def failed(error):
            self._preference_inflight=False;self._preference_error=error;self._preference_yield=True
            LOG.error('Could not save queued UI preferences: %s',error)
            flash=getattr(w,'_flash_status',None)
            if callable(flash):flash('Preferences NOT saved — check space/permissions and retry',20)
        try:accepted=self._submit(replacements,removals,committed,failed,publish_gui=False)
        except (OSError,ValueError,TypeError) as exc:
            failed(exc);return False
        if not accepted:
            self._preference_inflight=False
            return False
        return True

    def flush_preferences(self):
        """Final teardown flush stays worker-owned even after ordinary owners stop."""
        self._preference_timer.stop()
        if self.owner.pending:self.owner.drain()
        if not self._preferences:return not self._preference_inflight
        if self.owner.stopping:
            self.owner=TaskOwner(self);self.owner.drained.connect(self._pump_preferences)
        if not self._pump_preferences(allow_shutdown=True):return False
        self.owner.drain()
        return not self.preferences_pending

    def submit(self,replacements,removals,committed,failed,*,current=None):
        return self._submit(replacements,removals,committed,failed,current=current)

    def queue_checkpoint(self,capture,completed,failed,*,current,validate=None):
        """Freeze selected data at one canonical FIFO position, not backlog end.

        ``capture`` runs on the GUI owner only after the current native/canonical
        ACK releases its token, and returns payload, expected leaves and absent
        paths. The worker verifies those leaves; admission is never success.
        """
        row=dict(token=object(),capture=capture,completed=completed,failed=failed,current=current,validate=validate,
                 epoch=self._epoch(),cancelled=False)
        self._checkpoints.append(row)
        QTimer.singleShot(0,self,self._pump_checkpoints)
        return row['token']

    def cancel_checkpoint(self,token):
        for row in self._checkpoints:
            if row['token'] is token:
                row['cancelled']=True
                worker=row.get('worker')
                if worker is not None:worker.cancel()
                self._pump_checkpoints();return True
        return False

    def _pump_checkpoints(self):
        if not self._checkpoints or self._active:return False
        w=self.window;row=self._checkpoints[0]
        def is_current():
            try:return row['current']() is True
            except Exception:
                LOG.exception('Could not verify current export checkpoint ownership')
                return False
        def reject(error):
            if self._checkpoints and self._checkpoints[0] is row:self._checkpoints.popleft()
            row['failed'](error)
            QTimer.singleShot(0,self,self._pump_checkpoints)
        try:
            if row['cancelled']:
                reject(StateChangedError('Export capture cancelled before admission'));return False
            if row['epoch']!=self._epoch() or not is_current():
                reject(StateChangedError('Library or device changed before export capture'));return False
            if w._shutting_down or self.owner.stopping:
                reject(StateChangedError('Export capture stopped during shutdown'));return False
            if w._state_maintenance or getattr(w,'_state_publication',False):return False
            token=row['token'];tags=w.tag_controller
            if not tags.reserve_state_commit(token):return False
            self.token=token;w._state_publication=True
            try:payload,expected,missing=row['capture']()
            except Exception as exc:
                if self._checkpoints and self._checkpoints[0] is row:self._checkpoints.popleft()
                self.token=None;w._state_publication=False;tags.release_state_commit(token)
                reject(exc);return False
            repository=w._state_repository
            worker=_CheckpointTask(repository,expected,tuple(missing),payload,row['validate'])
            row['worker']=worker
            def complete(task):
                if self.token is not token:return
                try:
                    w._state_publication=False
                    if isinstance(task.result,Exception):row['failed'](task.result)
                    elif row['cancelled'] or row['epoch']!=self._epoch() or not is_current():
                        row['failed'](StateChangedError('Export capture no longer owns its request'))
                    elif not repository.current_verification(task.result):
                        row['failed'](StateChangedError('Canonical state changed before export capture completed'))
                    else:row['completed'](task.payload)
                finally:
                    if self._checkpoints and self._checkpoints[0] is row:self._checkpoints.popleft()
                    self.token=None;tags.release_state_commit(token);self._schedule_preferences()
            self.owner.adopt(worker,complete);worker.start();return True
        except Exception as exc:
            reject(exc);return False

    def _submit(self,replacements,removals,committed,failed,*,publish_gui=True,current=None):
        w=self.window
        if self._active or getattr(w,'_state_publication',False):return False
        def is_current():
            try:return current is None or current() is True
            except Exception:
                LOG.exception('Could not verify current state publication ownership')
                return False
        if not is_current():
            failed(StateChangedError('Library details changed before saving'));return True
        removals=tuple(removals);paths=(*replacements,*removals)
        if any(not path or path[0] not in _ROOTS for path in paths):
            raise ValueError('Metadata commit exceeds its canonical state roots')
        if any(len(path)<2 for path in removals):
            raise ValueError('Metadata commit cannot remove a canonical state root')
        repository=w._state_repository
        patch=repository.prepare_patch(replacements,removals)
        token=object();self.token=token;w._state_publication=True
        tags=w.tag_controller
        if not tags.reserve_state_commit(token):
            self.token=None;w._state_publication=False;return False

        def complete(result):
            if self.token is not token:return
            try:
                # Worker has joined. Release the state interlock before the
                # continuation; the separate source token stays owned until it
                # returns, so no other domain can interleave a native write.
                w._state_publication=False
                if isinstance(result,Exception):failed(result)
                elif w._state_repository is not repository or not repository.current_commit(result):
                    failed(StateChangedError('Library changed before saved details could be published'))
                elif not is_current():
                    failed(StatePublicationChangedError('Details were saved; newer library/view state was retained'))
                else:
                    if publish_gui:apply_gui_patch(w,result.patch)
                    if result.backup_warning:
                        LOG.warning('Library saved, but rotating backup failed: %s',result.backup_warning)
                    committed()
            finally:
                self.token=None
                tags.release_state_commit(token)
                self._schedule_preferences()

        if self.owner.stopping:
            # Shutdown/maintenance still reconciles already admitted source
            # work. Browsing has ended; never discard its durable continuation.
            try:result=repository.commit_patch(patch)
            except Exception as exc:result=exc
            complete(result);return True
        worker=_CommitTask(repository,patch)
        self.owner.adopt(worker,lambda task:complete(task.result))
        worker.start();return True

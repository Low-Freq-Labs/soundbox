"""Normal Quit retains exactly verified failed intentions and waits for saves."""
from copy import deepcopy

from PySide6.QtCore import QObject, QTimer

from soundbox.state_io import StateChangedError


QUEUES = ('shared_import_jobs', 'analysis_jobs', 'native_analysis_jobs',
          'pending_prep_edits', 'rekordbox_playlist_edits', 'pending_tag_definitions',
          'pending_tag_assignments', 'pending_metadata_edits', 'usb_settings_edits')
METADATA = ('pending_metadata_edits', 'source_truth_migration', 'source_truth_alias_next')


def failed_intentions(settings):
    """Only failed persisted-domain candidates; RAM drafts never enter this proof."""
    values = {key: value for key in QUEUES if isinstance(value := settings.get(key), list)
              and value and any(isinstance(row, dict) and row.get('error') for row in value)}
    if any(key in values for key in METADATA) or any(
            isinstance(settings.get(key), dict) and settings[key].get('error') for key in METADATA):
        values.update((key, settings[key]) for key in METADATA if settings.get(key))
    removal = settings.get('graveyard_delete_intent')
    if isinstance(removal, dict) and removal.get('error'):
        values['graveyard_delete_intent'] = removal
    return deepcopy(values)


class QuitSaves(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.requested = False
        self.checkpoint = None
        self.verified = None
        self.error = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.poll)

    def retained(self):
        if self.verified is None:return ()
        repository, proof, values = self.verified
        w = self.window
        if (w._state_repository is repository and repository.current_verification(proof)
                and all(w.settings.get(key) == value for key, value in values.items())):
            return tuple(values)
        self.verified = None
        return ()

    def defer(self, *, requested):
        from .app import MainWindow
        from .state_commit import StateCommitter
        w = self.window
        if requested:
            self.requested = True
            self.error = None
        if self.checkpoint is None and self.error is None:
            values = failed_intentions(w.settings)
            acknowledgements=[]
            for name in ('playlist_controller','tag_definition_controller','usb_settings_controller'):
                controller=(vars(w).get(name) if name=='usb_settings_controller' else getattr(w,name,None))
                ack=getattr(controller,'ack',None)
                captured=ack.retention_capture() if ack is not None else None
                if captured is not None:
                    acknowledgements.append((ack,captured));values[ack.key]=deepcopy(captured['queue'])
            remaining = MainWindow._pending_save_reason(w, retained=values)
            if values and not remaining:
                repository = getattr(w, '_state_repository', None)
                if repository is not None:
                    if getattr(w, '_state_committer', None) is None:w._state_committer = StateCommitter(w)
                    owner = object(); self.checkpoint = owner
                    expected={('settings',key):value for key,value in values.items()
                              if key not in {ack.key for ack,_captured in acknowledgements}}
                    def current():
                        return bool(self.checkpoint is owner and not w._shutting_down
                            and w._state_repository is repository
                            and all(w.settings.get(key) == value for key, value in values.items())
                            and all(ack.retention_current(captured) for ack,captured in acknowledgements))
                    def complete(payload, proof):
                        def finish():
                            if getattr(w._state_committer,'_active',False):
                                QTimer.singleShot(10,self,finish);return
                            if not current() or not repository.current_verification(proof):
                                failed(StateChangedError('Saved source acknowledgement changed before Quit'));return
                            for ack,captured in acknowledgements:
                                if not ack.release_retained(captured,payload[ack.key]):
                                    failed(StateChangedError('Saved source acknowledgement lost its Quit owner'));return
                            self.checkpoint = None
                            self.verified = repository, proof, payload
                            if self.requested:self.poll()
                        QTimer.singleShot(0,self,finish)
                    def failed(error):
                        self.checkpoint = None; self.error = error; self.requested = False
                        w._flash_status('Could not verify saved edits for Quit — ' + str(error), 20)
                    def capture():
                        return deepcopy(values), expected, ()
                    def validate(payload):
                        with repository.area.lock:
                            if (repository.recovery is not None or not repository.writable
                                    or repository.area.journal.exists()):
                                raise StateChangedError('Canonical recovery must finish before saved edits can be retained')
                            for ack,captured in acknowledgements:
                                for candidate in (captured['before'],captured['desired']):
                                    try:repository.verify_selected_values({('settings',ack.key):candidate})
                                    except StateChangedError:continue
                                    expected[('settings',ack.key)]=deepcopy(candidate)
                                    payload[ack.key]=deepcopy(candidate)
                                    break
                                else:raise StateChangedError('Saved source queue differs from both verified ACK outcomes')
                        return payload
                    w._state_committer.queue_checkpoint(capture, complete, failed, current=current,
                                                        validate=validate, with_verification=True)
                else:
                    self.requested = False  # No canonical proof can authorize this close.
            elif 'attention' in remaining.lower():
                self.requested = False  # RAM/storage errors need explicit recovery.
        if self.requested and self.error is None:self.timer.start(50)

    def poll(self):
        from .app import MainWindow
        w = self.window
        if not self.requested or w._shutting_down:return
        if MainWindow._defer_quit_for_saves(w):
            return  # defer() owns the next wakeup; failures remain visible.
        self.requested = False
        w.close()

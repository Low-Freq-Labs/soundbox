"""Playlist/device selection and one queue over the existing guarded exporters."""
from dataclasses import replace
import logging
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from soundbox.manifest import playlist_key
from .devices import device_export_readiness, device_facts, human
from . import theme


class DeviceRequestNotStarted(RuntimeError):
    """A desktop request refused before any device backend was admitted."""

    def __init__(self,message):
        super().__init__(message)
        self.recovery_status='not needed'


def stock_export_summary(plan):
    """Expected format omissions are a result summary, not a failed export."""
    count=getattr(plan,'source_track_count',None)
    if type(count) is not int:return '', ''
    omitted=tuple(getattr(plan,'omissions',()))
    suffix=f' · {count} songs' + (f' · {len(omitted)} skipped' if omitted else '')
    details=''
    if omitted:
        details='\n\nSkipped for this iPod format:\n'+'\n'.join(
            f'{row.title}: {row.reason}' for row in omitted[:20])
        if len(omitted)>20:details+=f'\n…and {len(omitted)-20} more'
    return suffix,details


def device_result_warnings(result):
    notices=[]
    for obj in (result,getattr(result,'stock',None)):
        if obj is None:continue
        notices.extend(str(getattr(obj,key,'')) for key in ('shadow_warning','artwork_warning','catalog_warning')
                       if getattr(obj,key,''))
        cleanup=tuple(map(str,getattr(obj,'cleanup_failures',())))
        if cleanup:notices.append('Some device files could not be removed:\n'+'\n'.join(cleanup))
    return tuple(dict.fromkeys(notices))


def device_result_notices(result):
    """Optional association limits are diagnostics, not failed device work."""
    return tuple(dict.fromkeys(str(getattr(obj,'catalog_notice',''))
        for obj in (result,getattr(result,'stock',None))
        if obj is not None and getattr(obj,'catalog_notice','')))


def device_attention_text(issues):
    """Keep the failure visible without repeating a successful export report."""
    notes=tuple(dict.fromkeys(str(issue).strip() for issue in issues if str(issue).strip()))
    shown=[];clipped=False
    for note in notes[:3]:
        lines=note.splitlines()
        brief='\n'.join(lines[:5])
        if len(brief)>500:
            brief=brief[:497]+'…'
            clipped=True
        if len(lines)>5:clipped=True
        shown.append(brief)
    if len(notes)>3:clipped=True
    if clipped:shown.append('Additional failure details are retained in the export log.')
    return '\n\n'.join(shown) or 'The device operation needs attention. Review the export log before retrying.'


def device_failure_outcome(error,*,phase='unknown',committed=False):
    """Only transaction evidence establishes a commit or completed rollback."""
    if committed or getattr(error,'committed',False):
        return 'Device changes were committed; the operation did not finish.'
    recovery=getattr(error,'__dict__',{}).get('recovery_status')
    if recovery=='not needed':
        return 'The operation stopped before device writes. No device files were changed.'
    if recovery=='rolled back':
        return 'The device rollback completed.'
    if recovery=='recovery required':
        return ('The device rollback was incomplete. Keep the recovery files; '
                'recovery needs review before retrying.')
    if phase=='plan':return 'No device files were changed.'
    return 'Device recovery status could not be established. Check the device status before retrying.'


def device_failure_text(error,*,phase='unknown',committed=False):
    """Bound the popup while retaining its full cause and outcome in the log."""
    reason=str(error) or type(error).__name__
    outcome=device_failure_outcome(error,phase=phase,committed=committed)
    traceback=getattr(error,'__traceback__',None)
    recovery=getattr(error,'recovery_directory',None) or getattr(getattr(error,'result',None),'recovery_directory',None)
    logging.getLogger('soundbox').error('Device failure (phase=%s, committed=%s, recovery=%r, recovery_directory=%r): %s\n%s',
        phase,bool(committed or getattr(error,'committed',False)),getattr(error,'recovery_status',None),
        recovery,reason,outcome,exc_info=(type(error),error,traceback) if traceback is not None else None)
    return device_attention_text((reason,outcome))


def present_ipod_failure(host,device,title,error,*,phase='unknown',committed=False,critical=False,guidance=''):
    """An owned export batch presents one terminal failure, not two dialogs."""
    committed=bool(committed or getattr(error,'committed',False))
    message=device_failure_text(error,phase=phase,committed=committed)
    if guidance:
        message+='\n\n'+guidance
        logging.getLogger('soundbox').error('%s: %s',title,guidance)
    host._last_device_failure=dict(device=device,title=title,diagnostic=str(error),
        outcome=device_failure_outcome(error,phase=phase,committed=committed),
        recovery=getattr(error,'recovery_status',None),committed=committed,guidance=guidance)
    batch=getattr(host,'_export_batch',None)
    target=batch.get('active') if batch else None
    if (target and target.get('path')==device
            and target.get('kind') in {'stock_ipod','dual_boot','rockbox'}):
        failure=error if isinstance(error,Exception) else RuntimeError(str(error))
        if committed and not getattr(failure,'committed',False):
            from soundbox.operation_control import CommittedOperationError
            failure=CommittedOperationError(str(error))
        host._export_target_result(device,failure)
        if batch.get('outcome') in {'failed','attention'}:
            key='note' if batch.get('outcome')=='attention' else 'error'
            batch[key]=device_failure_outcome(error,phase=phase,committed=committed)+(
                '\n'+guidance if guidance else '')+'\n\n'+str(error)
        return False
    (QMessageBox.critical if critical else QMessageBox.warning)(host,title,message)
    return True


def persist_ipod_export_witness(result,store,device_key):
    """Save only engine-verified readback evidence, off the GUI thread."""
    from soundbox.stock_ipod import StockIpodCommitResult, StockIpodDeleteResult
    from soundbox.stock_ipod_catalog_state import StockCatalogWitness
    stock=getattr(result,'stock',result)
    if not isinstance(stock,(StockIpodCommitResult,StockIpodDeleteResult)):return result
    witness=stock.catalog_witness
    if witness is None:return result
    try:
        if not isinstance(witness,StockCatalogWitness) or witness.device_key!=device_key:
            raise ValueError('The returned comparison cache belongs to another iPod')
        store.save(witness)
    except Exception as exc:
        warning='Device changes saved; the optional Sync comparison cache could not be saved: '+str(exc)
        revised=replace(stock,catalog_warning='\n'.join(filter(None,(stock.catalog_warning,warning))))
        return replace(result,stock=revised) if stock is not result else revised
    return result


def verify_ipod_export_capture(capture,art_cache):
    """Worker-side selected fences and artwork selection, never GUI state."""
    if capture is None:return
    capture.verify_worker()
    if capture.artwork is not None and not art_cache.selection_current(capture.artwork):
        raise ValueError('Selected artwork changed; review it before exporting')


def captured_ipod_noop_result(plan,store):
    """Read back a no-write generation before saving its optional association."""
    from soundbox.dual_ipod import DualIpodSyncPlan,commit_dual_ipod_sync
    from soundbox.stock_ipod import StockIpodCommitResult,committed_stock_export_witness
    from soundbox.sync import DeviceSession
    if isinstance(plan,DualIpodSyncPlan):
        result=commit_dual_ipod_sync(plan)
        stock_plan=plan.stock
        if stock_plan.catalog_witness is not None and result.stock.catalog_witness is None:
            raise ValueError(result.stock.catalog_warning or 'The iPod changed after its read-only comparison')
    else:
        session=DeviceSession(plan.target,[],create=False)
        witness,warning=committed_stock_export_witness(plan,session)
        if plan.catalog_witness is not None and witness is None:
            raise ValueError(warning or 'The iPod changed after its read-only comparison')
        stock_plan=plan
        result=StockIpodCommitResult(plan.target/'.soundbox',0,0,plan.reused_track_count,0,
            plan.playlist_names,plan.database_sha256_before,catalog_witness=witness,
            catalog_warning='\n'.join(filter(None,(plan.catalog_warning,warning))),
            catalog_notice=plan.catalog_notice)
    return persist_ipod_export_witness(result,store,stock_plan.identity_key)


def _device_icon(ipod):
    """Neutral, scalable device outlines; no system icons or device reads."""
    pixmap=QPixmap(96,96);pixmap.setDevicePixelRatio(2);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(theme.TEXT),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if ipod:
        painter.drawRoundedRect(QRectF(9,2,30,44),4,4)
        painter.drawRoundedRect(QRectF(13,7,22,15),1,1)
        painter.drawEllipse(QRectF(16,27,16,16));painter.drawEllipse(QRectF(21,32,6,6))
    else:
        painter.drawRoundedRect(QRectF(12,17,24,29),4,4)
        painter.drawRect(QRectF(16,3,16,14))
        painter.drawLine(21,7,21,11);painter.drawLine(27,7,27,11)
    painter.end()
    return QIcon(pixmap)


class _DeviceChoices(QTreeWidget):
    """Fill the panel evenly with up to five generous device choices."""
    def resizeEvent(self,event):
        super().resizeEvent(event)
        slots=max(1,min(5,self.topLevelItemCount()))
        height=max(68,self.viewport().height()//slots)
        for at in range(self.topLevelItemCount()):
            self.topLevelItem(at).setSizeHint(0,QSize(0,height))


class ExportDialog(QDialog):
    def __init__(self, playlists, devices, *, preferred='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Export');self.resize(800,600)
        self.setMinimumSize(640,400)
        self._playlists={};self._targets={}
        layout=QVBoxLayout(self);layout.setContentsMargins(16,12,16,12)
        intro=QLabel('Choose playlists and where to export them.')
        intro.setObjectName('SettingsSub');layout.addWidget(intro)
        columns=QHBoxLayout();columns.setSpacing(16);layout.addLayout(columns,1)
        self.playlists=self._column(columns,'Playlists',3)
        self.devices=self._column(columns,'Connected devices',2,devices=True)
        folders={}
        for playlist in playlists:
            key=playlist_key(playlist)
            if key in self._playlists:continue
            parent_item=self.playlists.invisibleRootItem()
            for at,name in enumerate(playlist.folder_path):
                path=tuple(playlist.folder_path[:at+1])
                if path not in folders:
                    item=QTreeWidgetItem(parent_item,[name])
                    item.setFlags(item.flags()|Qt.ItemIsUserCheckable|Qt.ItemIsAutoTristate)
                    item.setCheckState(0,Qt.Unchecked);folders[path]=item
                parent_item=folders[path]
            item=QTreeWidgetItem(parent_item,[playlist.name])
            item.setData(0,Qt.UserRole,key)
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
            item.setCheckState(0,Qt.Unchecked)
            item.setToolTip(0,playlist.display_path)
            self._playlists[key]=item
        selectable = [target for target in devices if target.get('selectable', True)]
        for target in devices:
            path=target['path']
            reason=target.get('reason', '')
            item=QTreeWidgetItem(self.devices,[target['name'] + (f' · {reason}' if reason else '')])
            available=target.get('selectable', True)
            item.setFlags((item.flags()|Qt.ItemIsUserCheckable) if available else
                          (item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled))
            item.setIcon(0,_device_icon(target.get('kind') in ('stock_ipod','dual_boot','rockbox')))
            item.setCheckState(0,Qt.Checked if available and (len(selectable)==1 or path==preferred) else Qt.Unchecked)
            item.setToolTip(0,f"{target['label']} · {human(target['free'])} free\n{path}"
                            + (f'\n{reason}' if reason else ''))
            self._targets[path]=(item,target)
        if not devices:
            QTreeWidgetItem(self.devices,['Connect a writable USB or iPod'])
            self.devices.setEnabled(False)
        footer=QHBoxLayout();self.summary=QLabel();self.summary.setObjectName('SettingsSub')
        footer.addWidget(self.summary);footer.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.Cancel)
        self.export_button=buttons.addButton('Export',QDialogButtonBox.AcceptRole)
        self.export_button.setDefault(True)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        footer.addWidget(buttons);layout.addLayout(footer)
        self.playlists.itemChanged.connect(self._changed)
        self.devices.itemChanged.connect(self._changed)
        self._changed()

    @staticmethod
    def _column(layout,title,stretch,*,devices=False):
        card=QWidget();card.setObjectName('TrackDetails');box=QVBoxLayout(card)
        box.setContentsMargins(12,12,12,12)
        caption=QLabel(title);box.addWidget(caption)
        tree=_DeviceChoices() if devices else QTreeWidget()
        tree.setObjectName('ExportDevices' if devices else 'ExportPlaylists')
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.setUniformRowHeights(True);tree.setIndentation(18)
        if devices:
            tree.setRootIsDecorated(False);tree.setIndentation(0)
            tree.setIconSize(QSize(44,44))
        box.addWidget(tree,1);layout.addWidget(card,stretch)
        return tree

    def selection(self):
        keys=[key for key,item in self._playlists.items() if item.checkState(0)==Qt.Checked]
        targets=[target for item,target in self._targets.values()
                 if target.get('selectable', True) and item.checkState(0)==Qt.Checked]
        return keys,targets

    def _changed(self,*_):
        keys,targets=self.selection()
        self.summary.setText(f"{len(keys)} {'playlist' if len(keys)==1 else 'playlists'} · {len(targets)} {'device' if len(targets)==1 else 'devices'}")
        self.export_button.setEnabled(bool(keys and targets))


class ExportSelectionMixin:
    def _export_playlists(self, *, include_views=True):
        if self.lib is None:return []
        extras=self._sync_playlists() if include_views else self._custom_playlists()
        return list({playlist_key(p):p for p in [*self.lib.playlists,*extras]}.values())

    def _show_export_dialog(self, preferred=''):
        if self.lib is None or self._device_operation_owned('') or getattr(self,'_export_batch',None):return
        binding=(str(self.lib.source),str(self.lib.source_library_id))
        targets=[]
        for path,profile in self._device_profiles.items():
            if (getattr(self, '_usb_format_device', '')
                    and path in getattr(self, '_usb_format_volumes', (self._usb_format_device,))):continue
            facts=device_facts(path,cached_only=True)
            token=self._device_mount_tokens.get(path,0)
            cached=getattr(self,'_stock_view_cache',{}).get(path)
            readiness=device_export_readiness(profile,facts,token,
                stock_snapshot=cached[0] if cached is not None else None)
            row=self._usb_device_rows.get(path)
            targets.append(dict(path=path,name=row[0] if row else Path(path).name,
                label=profile.label,free=row[1] if row else facts.free_bytes,
                token=token,uuid=facts.volume_uuid,kind=profile.kind,
                selectable=readiness.selectable,reason=readiness.reason))
        if not targets:
            self._flash_status('Connected devices are being formatted; wait for completion'
                if self._device_profiles else 'Connect a USB or iPod to export')
            return
        dialog=ExportDialog(self._export_playlists(),targets,preferred=preferred,parent=self)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            keys,targets=dialog.selection()
            keys=list(keys);targets=[dict(target) for target in targets]
        finally:
            dialog.deleteLater()
        targets=[target for target in targets if target.get('selectable', True)]
        if not keys or not targets:return
        self._export_batch=dict(binding=binding,keys=keys,pending=list(targets),active=None,
            results=[],outcomes=[],cancelled=False,error='',started=False)
        self._update_device_action_state()
        self._start_next_export_target()

    def _start_next_export_target(self):
        batch=getattr(self,'_export_batch',None)
        if batch is None or self._shutting_down or batch['active'] is not None:return
        from .cdj import _source_edits_pending,_source_edits_error
        # Generic USB retains its source-save contract. Each iPod target uses
        # its own selected, durable capture and must not inherit another
        # target's native migration error or paused backlog.
        while (not batch['cancelled'] and batch['pending']
               and batch['pending'][0]['kind'] not in {'stock_ipod','dual_boot','rockbox'}):
            error=_source_edits_error(self)
            if not error:break
            target=batch['pending'].pop(0)
            batch['results'].append((target['name'],error))
            batch.setdefault('outcomes',[]).append('failed')
        if batch['cancelled'] or not batch['pending']:
            self._export_batch=None
            # Source waiting owns no device; a different command may now own
            # the progress controls. Ending this batch must not hide its work.
            if not self._device_operation_owned():
                self._stop_pulse();self.cancel_btn.setVisible(False);self.progress.setVisible(False)
            self._update_device_action_state()
            outcomes=batch.get('outcomes',[])
            completed=sum(outcome in {'complete','attention'} for outcome in outcomes)
            failed=any(outcome in {'failed','attention'} for outcome in outcomes)
            cancelled=batch['cancelled'] or 'cancelled' in outcomes
            self._last_export_batch_results=tuple(batch['results'])
            if not failed and (completed or cancelled):
                summaries=tuple(batch.get('summaries',{}).values())
                summary=(summaries[0][1] if len(summaries)==1 and completed==1 else
                         ' · '+'; '.join(name+text for name,text in summaries) if summaries else '')
                self._flash_status(('Export cancelled' if cancelled else 'Export complete')
                    + (f' — {completed} target(s) completed' if completed else '')
                    + (summary if not cancelled else ''),15)
            else:
                issues=[f'{name}: {note}' for index,(name,note) in enumerate(batch['results'])
                        if index>=len(outcomes) or outcomes[index] in {'failed','attention'}]
                QMessageBox.warning(self,'Export needs attention',
                    device_attention_text(issues or ['No targets were exported.']))
            if getattr(self, '_usb_format_device', ''):self._show_usb_format_status()
            return
        if self._device_operation_owned(''):
            QTimer.singleShot(100,self._start_next_export_target)
            return
        if (batch['pending'][0]['kind'] not in {'stock_ipod','dual_boot','rockbox'}
                and _source_edits_pending(self)):
            self._start_pulse('Waiting for source edits')
            self.cancel_btn.setVisible(True)
            QTimer.singleShot(100,self._start_next_export_target)
            return
        target=batch['pending'].pop(0);batch['active']=target
        batch['error']='';batch['started']=False;batch['outcome']='';batch['note']=''
        try:
            if self.lib is None or (str(self.lib.source),str(self.lib.source_library_id))!=batch['binding']:
                raise ValueError('Source library changed; choose playlists again')
            available={playlist_key(p):p for p in self._export_playlists()}
            if any(key not in available for key in batch['keys']):
                raise ValueError('A selected playlist was removed; choose playlists again')
            playlists=[available[key] for key in batch['keys']]
            dev=target['path']
            self._validate_export_target(dev,device_facts(dev,cached_only=True))
            if target['kind']=='generic_usb':
                self._begin_cdj_export(dev,playlists,from_drop=True,drop_target=(target['token'],target['uuid']))
            elif target['kind']=='stock_ipod':
                catalog_selections=()
                if getattr(self,'_stock_view_cache',{}).get(dev):
                    from .stock_catalog_sync import source_sync_selection
                    catalog_selections=source_sync_selection(self,dev,playlists)
                if catalog_selections:
                    from .stock_catalog_sync import request_sync
                    request_sync(self,catalog_selections,device=dev,confirmed=True,
                                 source_playlists=playlists)
                else:
                    self._begin_stock_ipod_sync(dev,playlists)
            elif target['kind']=='dual_boot':
                self._begin_dual_ipod_sync(dev,playlists)
            else:
                self._on_device_drop(dev,[('rb',p.display_path) if p.pid else ('sbview','collection') if p.name=='Collection'
                    else ('sbview','artists') if p.name=='Artists' else ('cust',p.name) for p in playlists],_from_export=True)
            source_wait = any(command.get('batch') is batch and command.get('batch_target') is target
                              for command in (getattr(self, '_source_sync_waiting', None) or ()))
            batch['started']=bool(self._device_operation_owned('') or self._cdj_waiting or source_wait)
            if not batch['started']:batch['error']='Export did not start or was cancelled'
        except Exception as exc:
            batch['error']=str(exc)
            if self.lib is None or (str(self.lib.source),str(self.lib.source_library_id))!=batch['binding']:
                batch['results'].extend((remaining['name'],batch['error']) for remaining in batch['pending'])
                batch.setdefault('outcomes',[]).extend('failed' for _ in batch['pending'])
                batch['pending'].clear()
        self._maybe_finish_export_target()

    def _validate_export_target(self,dev,facts):
        batch=getattr(self,'_export_batch',None)
        target=batch['active'] if batch else None
        if target and target['path']==dev:
            profile=self._device_profiles.get(dev)
            if ((getattr(self, '_usb_format_device', '') and
                    dev in getattr(self, '_usb_format_volumes', (self._usb_format_device,)))
                    or not profile or profile.kind!=target['kind'] or not facts.writable
                    or (self._device_mount_tokens.get(dev,0),facts.volume_uuid)!=(target['token'],target['uuid'])):
                raise ValueError('Device disconnected or changed; select the connected device again')

    def _maybe_finish_export_target(self):
        batch=getattr(self,'_export_batch',None)
        if (batch is None or batch['active'] is None or self._shutting_down
                or self._device_operation_owned('') or self._cdj_waiting):return
        if any(command.get('batch') is batch and command.get('batch_target') is batch['active']
               for command in (getattr(self, '_source_sync_waiting', None) or ())):return
        target=batch['active']
        outcome=batch.get('outcome') or ('cancelled' if batch['cancelled'] else
            'failed' if batch['error'] or not batch['started'] else 'complete')
        note=batch.get('note') or batch['error'] or ('Cancelled' if outcome=='cancelled' else
            'Export complete' if outcome=='complete' else 'Export did not start')
        batch['results'].append((target['name'],note))
        batch.setdefault('outcomes',[]).append(outcome)
        batch['active']=None
        QTimer.singleShot(0,self._start_next_export_target)

    def _export_target_result(self,dev,result):
        batch=getattr(self,'_export_batch',None)
        if not (batch and batch['active'] and batch['active']['path']==dev):return
        from soundbox.report import SyncReport
        if isinstance(result,SyncReport):
            self._export_target_result(dev,[(dev,result)])
            return
        if isinstance(result,Exception):
            from soundbox.operation_control import CommittedOperationError,OperationCancelled
            if isinstance(result,CommittedOperationError) or getattr(result,'committed',False):
                device_failure_text(result)
                batch['outcome']='attention';batch['note']=device_failure_outcome(result)+'\n\n'+str(result)
                return
            cancelled=(isinstance(result,OperationCancelled)
                and not getattr(result,'committed',False)
                and getattr(result,'recovery_status',None) in {None,'not needed','rolled back'})
            batch['error']=str(result) or ('Cancelled' if isinstance(result,OperationCancelled) else 'Export failed')
            if (not cancelled
                    and batch['active'].get('kind') in {'stock_ipod','dual_boot','rockbox'}):
                device_failure_text(result)
                if not (isinstance(result,DeviceRequestNotStarted)
                        and result.__dict__.get('recovery_status')=='not needed'):
                    batch['error']=device_failure_outcome(result)+'\n\n'+batch['error']
            batch['outcome']='cancelled' if cancelled else 'failed'
            batch['note']=''
            return
        if isinstance(result,list):
            selected=[report for path,report in result if path in {dev,'(worker)'}]
            errors=[report for report in selected if isinstance(report,str)]
            reports=[report for report in selected if isinstance(report,SyncReport)]
            errors.extend(error for report in reports for playlist in report.playlists for error in playlist.errors)
            errors.extend(f'{playlist.name}: {playlist.missing} missing song(s), {playlist.conflicts} conflicting file(s)'
                for report in reports for playlist in report.playlists if playlist.missing or playlist.conflicts)
            if errors:
                batch['outcome']='failed';batch['error']='; '.join(errors)
            elif any(report.cancelled for report in reports) or not result and getattr(self,'_cancelling',False):
                batch['outcome']='cancelled';batch['note']='Cancelled; any verified partial export is retained for retry'
            elif reports:
                batch['outcome']='complete'
            elif not result:
                batch['outcome']='failed';batch['error']='The export returned no result; retry this target'
            return
        from soundbox.stock_ipod import StockIpodCommitResult
        stock=getattr(result,'stock',result)
        if not isinstance(stock,StockIpodCommitResult):return
        warnings=device_result_warnings(result)
        if warnings:
            batch['outcome']='attention';batch['note']='Export saved — needs attention:\n'+'\n'.join(dict.fromkeys(warnings))

    def _export_target_complete(self,dev,note='Export complete'):
        batch=getattr(self,'_export_batch',None)
        if (batch and batch['active'] and batch['active']['path']==dev
                and batch.get('outcome') not in {'failed','cancelled'}):
            if batch.get('outcome')=='attention':
                batch['note']=note
            else:batch['outcome']='complete';batch['note']=note

    def _request_ipod_export_capture(self,kind,dev,playlists,**options):
        """Keep an asynchronous checkpoint bound to its original batch target."""
        from .ipod_export_capture import request_ipod_export
        batch=getattr(self,'_export_batch',None)
        target=batch.get('active') if batch else None
        context=dict(batch=batch if target and target.get('path')==dev else None,
                     batch_target=target)
        def failed(error,request):
            from .export_wait import retire_source_presentation
            batch=request.get('batch');target=request.get('batch_target')
            if batch is not None:
                if getattr(self,'_export_batch',None) is not batch or batch.get('active') is not target:return
                retire_source_presentation(self)
                self._export_target_result(dev,error)
                self._maybe_finish_export_target()
            elif not self._shutting_down:
                retired=retire_source_presentation(self)
                if getattr(error,'choose_playlists',False):
                    QTimer.singleShot(0,lambda:self._show_export_dialog(preferred=dev)
                                      if not self._shutting_down and self.lib is not None else None)
                else:
                    notice=self._flash_status if retired else getattr(self,'_background_notice',self._flash_status)
                    brief=device_failure_text(error,phase='plan').splitlines()[0]
                    notice('iPod export needs attention — '+(brief[:237]+'…' if len(brief)>240 else brief),20)

        def ready(capture):
            try:
                capture.require_current(self,dev)
                selected=list(capture.selected_playlists)
                getattr(self,f'_begin_{kind}_ipod_sync')(
                    dev,selected,_export_capture=capture,**options)
            except Exception as exc:
                failed(exc,context)

        try:
            return request_ipod_export(self,kind,dev,playlists,options=options,ready=ready,failed=failed)
        except Exception as exc:
            failed(exc,context)
            return True


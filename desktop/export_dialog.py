"""Playlist/device selection and one queue over the existing guarded exporters."""
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from soundbox.manifest import playlist_key
from .devices import device_facts, human
from . import theme


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
        for target in devices:
            path=target['path']
            item=QTreeWidgetItem(self.devices,[target['name']])
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
            item.setIcon(0,_device_icon(target.get('kind') in ('stock_ipod','dual_boot','rockbox')))
            item.setCheckState(0,Qt.Checked if len(devices)==1 or path==preferred else Qt.Unchecked)
            item.setToolTip(0,f"{target['label']} · {human(target['free'])} free\n{path}")
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
        targets=[target for item,target in self._targets.values() if item.checkState(0)==Qt.Checked]
        return keys,targets

    def _changed(self,*_):
        keys,targets=self.selection()
        self.summary.setText(f"{len(keys)} {'playlist' if len(keys)==1 else 'playlists'} · {len(targets)} {'device' if len(targets)==1 else 'devices'}")
        self.export_button.setEnabled(bool(keys and targets))


class ExportSelectionMixin:
    def _export_playlists(self):
        if self.lib is None:return []
        return list({playlist_key(p):p for p in [*self.lib.playlists,*self._sync_playlists()]}.values())

    def _show_export_dialog(self, preferred=''):
        if self.lib is None or self._device_operation_owned('') or getattr(self,'_export_batch',None):return
        binding=(str(self.lib.source),str(self.lib.source_library_id))
        targets=[]
        for path,profile in self._device_profiles.items():
            if (getattr(self, '_usb_format_device', '')
                    and path in getattr(self, '_usb_format_volumes', (self._usb_format_device,))):continue
            facts=device_facts(path,cached_only=True)
            token=self._device_mount_tokens.get(path,0)
            capable=(profile.cdj_export or profile.stock_write or profile.rockbox_export)
            if not capable or not facts.writable or not facts.volume_uuid or not token:continue
            row=self._usb_device_rows.get(path)
            targets.append(dict(path=path,name=row[0] if row else Path(path).name,
                label=profile.label,free=row[1] if row else facts.free_bytes,
                token=token,uuid=facts.volume_uuid,kind=profile.kind))
        if not targets:
            self._flash_status('Connect a writable USB or supported iPod to export')
            return
        dialog=ExportDialog(self._export_playlists(),targets,preferred=preferred,parent=self)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            keys,targets=dialog.selection()
            keys=list(keys);targets=[dict(target) for target in targets]
        finally:
            dialog.deleteLater()
        if not keys or not targets:return
        self._export_batch=dict(binding=binding,keys=keys,pending=list(targets),active=None,
            results=[],cancelled=False,error='',started=False)
        self._update_device_action_state()
        self._start_next_export_target()

    def _start_next_export_target(self):
        batch=getattr(self,'_export_batch',None)
        if batch is None or self._shutting_down:return
        if batch['cancelled'] or not batch['pending']:
            self._export_batch=None
            self._stop_pulse();self.cancel_btn.setVisible(False);self.progress.setVisible(False)
            self._update_device_action_state()
            lines=[f"{name}: {note}" for name,note in batch['results']]
            if batch['cancelled']:lines.append('Remaining exports cancelled.')
            QMessageBox.information(self,'Export finished','\n'.join(lines))
            if getattr(self, '_usb_format_device', ''):self._show_usb_format_status()
            return
        if self._device_operation_owned(''):
            QTimer.singleShot(100,self._start_next_export_target)
            return
        from .cdj import _source_edits_pending
        if _source_edits_pending(self):
            self._start_pulse('Waiting for source edits')
            self.cancel_btn.setVisible(True)
            QTimer.singleShot(100,self._start_next_export_target)
            return
        target=batch['pending'].pop(0);batch['active']=target
        batch['error']='';batch['started']=False
        try:
            if (str(self.lib.source),str(self.lib.source_library_id))!=batch['binding']:
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
                self._begin_stock_ipod_sync(dev,playlists)
            elif target['kind']=='dual_boot':
                self._begin_dual_ipod_sync(dev,playlists)
            else:
                self._on_device_drop(dev,[('rb',p.display_path) if p.pid else ('sbview','collection') if p.name=='Collection'
                    else ('sbview','artists') if p.name=='Artists' else ('cust',p.name) for p in playlists],_from_export=True)
            batch['started']=bool(self._device_operation_owned('') or self._cdj_waiting)
            if not batch['started']:batch['error']='Export did not start or was cancelled'
        except Exception as exc:
            batch['error']=str(exc)
            if (str(self.lib.source),str(self.lib.source_library_id))!=batch['binding']:
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
        target=batch['active']
        batch['results'].append((target['name'],batch['error'] or ('Cancelled' if batch['cancelled'] else 'Export complete')))
        batch['active']=None
        QTimer.singleShot(0,self._start_next_export_target)

    def _export_target_result(self,dev,result):
        batch=getattr(self,'_export_batch',None)
        if batch and batch['active'] and batch['active']['path']==dev and isinstance(result,Exception):
            batch['error']=str(result)


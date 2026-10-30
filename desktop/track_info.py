"""Responsive track information; artwork resolves only after the dialog opens."""
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QGridLayout,QHBoxLayout,
    QLabel,QLineEdit,QMessageBox,QPlainTextEdit,QPushButton,QScrollArea,QTabWidget,QVBoxLayout,QWidget,QComboBox)
from soundbox.track_info import EDITABLE
from .task_owner import TaskOwner


class ArtworkWorker(QThread):
    def __init__(self, resolve):
        super().__init__();self.resolve=resolve;self.image=QImage()

    def run(self):
        try:
            path=self.resolve()
            if path and not self.isInterruptionRequested():
                self.image=QImage(str(path)).scaled(640,640,Qt.KeepAspectRatio,Qt.SmoothTransformation)
        except (OSError,ValueError):
            pass


class TrackInfoDialog(QDialog):
    def __init__(self, row, *, artwork=None, pick_art=None, save=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Track information');self.resize(800,760)
        self.row=dict(row);self.controls={};self.save_callback=save
        self._artwork=artwork;self._pick_art=pick_art;self._image=QImage()
        self._owner=TaskOwner(self);self._art_worker=None;self._info_built=False
        self._covers=[]
        layout=QVBoxLayout(self);layout.setContentsMargins(16,12,16,12)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        summary=QWidget();sv=QVBoxLayout(summary);sv.setSpacing(16)
        centered=QHBoxLayout();centered.addStretch()
        group=QWidget();group.setFixedWidth(700)
        top=QHBoxLayout(group);top.setContentsMargins(0,0,0,0);top.setSpacing(20)
        cover=self._cover(320);top.addWidget(cover);top.addSpacing(20)
        titles=QVBoxLayout();titles.setSpacing(6)
        for key,label in (('title','Track title'),('artist','Artist'),('album','Album')):
            caption=QLabel(label);caption.setObjectName('SettingsSub');titles.addWidget(caption)
            value=QLabel(str(row.get(key) or '—'));value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse);titles.addWidget(value)
        top.addLayout(titles,1);centered.addWidget(group);centered.addStretch();sv.addLayout(centered)
        details=QWidget();details.setObjectName('TrackDetails');form=QGridLayout(details)
        form.setContentsMargins(14,12,14,12);form.setHorizontalSpacing(18);form.setVerticalSpacing(10)
        for at,(key,label) in enumerate((('time','Time'),('format','File type'),('size','Size'),('dateCreated','Date created'),
                ('sampleRate','Sample rate'),('bitrate','Bitrate'),('playCount','DJ play count'),('location','Location'))):
            value=str(row.get(key) or '—')
            if key=='size' and value.isdigit():
                from .devices import human
                value=human(int(value))
            elif key=='sampleRate' and value!='—':value+=' Hz'
            elif key=='bitrate' and value!='—':value+=' kbps'
            caption=QLabel(label);caption.setObjectName('SettingsSub')
            text=QLabel(value);text.setWordWrap(True);text.setMinimumWidth(0)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if key=='location':text.setToolTip(value)
            form.addWidget(caption,at,0,Qt.AlignTop);form.addWidget(text,at,1)
        form.setColumnStretch(1,1);sv.addWidget(details);sv.addStretch()
        self.tabs.addTab(summary,'Summary');self.tabs.addTab(QWidget(),'Info')
        self.tabs.currentChanged.connect(self._tab_changed)
        actions=QHBoxLayout()
        note=QLabel('Edits are saved in SoundBox and used for USB export.');note.setObjectName('SettingsSub')
        note.setVisible(save is not None);actions.addWidget(note);actions.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.Close);buttons.rejected.connect(self.reject)
        if save:
            apply=buttons.addButton('Save',QDialogButtonBox.AcceptRole);apply.clicked.connect(self._save)
        actions.addWidget(buttons);layout.addLayout(actions)
        # No artwork state lock or image decode on the dialog-opening path.
        QTimer.singleShot(0,self,self._load_artwork)

    def _cover(self,size):
        cover=QLabel('No artwork');cover.setFixedSize(size,size);cover.setAlignment(Qt.AlignCenter)
        self._covers.append(cover);self._paint_artwork();return cover

    def _paint_artwork(self):
        for cover in self._covers:
            if self._image.isNull():cover.setText('No artwork')
            else:
                ratio=cover.devicePixelRatioF()
                image=QPixmap.fromImage(self._image).scaled(round(cover.width()*ratio),round(cover.height()*ratio),Qt.KeepAspectRatio,Qt.SmoothTransformation)
                image.setDevicePixelRatio(ratio);cover.setPixmap(image)

    def _load_artwork(self):
        if self._owner.stopping or self._art_worker is not None or self._artwork is None:return
        worker=ArtworkWorker(self._artwork);self._art_worker=worker
        def retired(w):
            self._art_worker=None
            if not self._owner.stopping:self._image=w.image;self._paint_artwork()
        self._owner.adopt(worker,retired);worker.start()

    def _tab_changed(self,index):
        if index!=1 or self._info_built:return
        self._info_built=True
        info=self.tabs.widget(1);iv=QVBoxLayout(info)
        iv.setSpacing(10)
        art=QHBoxLayout();art.addStretch();art.addWidget(self._cover(208));art.addSpacing(16)
        choose=QPushButton('Change artwork…');choose.setEnabled(self._pick_art is not None)
        def pick():
            if self._pick_art:self._pick_art();self._load_artwork()
        choose.clicked.connect(pick);art.addWidget(choose);art.addStretch();iv.addLayout(art)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.NoFrame)
        host=QWidget();grid=QGridLayout(host);grid.setContentsMargins(0,2,0,2)
        grid.setHorizontalSpacing(12);grid.setVerticalSpacing(8)
        visible=[('title','Track title'),('artist','Artist'),('album','Album'),('year','Year'),
            ('releaseDate','Release date'),('bpm','BPM'),('key','Key'),('playCount','DJ play count'),
            ('rating','Rating'),('genre','Genre'),('label','Label'),('color','Color'),
            ('hotCueAutoLoad','Hot cue auto load'),('publishInfo','Publish track information'),
            ('comments','Comments'),('message','Message'),('myTags','My Tag')]
        for at,(key,label) in enumerate(visible):
            field=QWidget();fl=QVBoxLayout(field);fl.setContentsMargins(0,0,0,0);fl.setSpacing(4)
            caption=QLabel(label);caption.setObjectName('SettingsSub');fl.addWidget(caption)
            control=QPlainTextEdit() if key=='comments' else QLineEdit()
            value=str(self.row.get(key) or ('0' if key in ('year','playCount','rating','color') else ''))
            if key in ('color','rating','hotCueAutoLoad'):
                control=QComboBox()
                choices=(list(enumerate(('None','Pink','Red','Orange','Yellow','Green','Aqua','Blue','Purple')))
                    if key=='color' else [(i,'Unrated' if i==0 else '★'*i) for i in range(6)] if key=='rating'
                    else [('Off','Off'),('On','On')])
                for token,text in choices:control.addItem(text,str(token))
                index=control.findData(value)
                if index<0:control.addItem(value,value);index=control.count()-1
                control.setCurrentIndex(index);control.setFixedHeight(28)
            elif isinstance(control,QPlainTextEdit):
                control.setObjectName('TrackComments');control.setPlainText(value);control.setFixedHeight(64)
            else:control.setText(value)
            control.setAccessibleName(label)
            editable=self.save_callback is not None and key in EDITABLE|{'title','artist'}
            if isinstance(control,QComboBox):control.setEnabled(editable)
            else:control.setReadOnly(not editable)
            if key=='bpm':control.setToolTip('Edit BPM with the loaded track’s beatgrid controls')
            if key=='releaseDate':control.setPlaceholderText('YYYY-MM-DD')
            if editable:self.controls[key]=control
            fl.addWidget(control)
            field.setProperty('infoField',key)
            if key=='title':grid.addWidget(field,0,0,1,2)
            elif key=='artist':grid.addWidget(field,0,2)
            elif key=='comments':grid.addWidget(field,5,0,1,3)
            elif key=='message':grid.addWidget(field,6,0)
            elif key=='myTags':grid.addWidget(field,6,1,1,2)
            else:grid.addWidget(field,1+(at-2)//3,(at-2)%3,Qt.AlignTop)
        for column in range(3):grid.setColumnStretch(column,1)
        grid.setRowStretch(7,1)
        scroll.setWidget(host);iv.addWidget(scroll,1)

    def done(self,result):
        self._owner.drain()
        super().done(result)


    def _save(self):
        changes={key:(control.toPlainText() if isinstance(control,QPlainTextEdit) else
                    control.currentData() if isinstance(control,QComboBox) else control.text())
                 for key,control in self.controls.items()}
        changes={key:value for key,value in changes.items() if value!=str(self.row.get(key) or (
            '0' if key in ('year','playCount','rating','color') else ''))}
        try:
            if changes and self.save_callback and not self.save_callback(changes):return
        except ValueError as exc:
            QMessageBox.warning(self,'Could not save information',str(exc));return
        self.accept()


class TrackInfoMixin:
    def _open_track_info(self):
        from .usb_device_view import CdjSong
        if self.stack.currentIndex()==1 and self.device_content_stack.currentIndex()==2:
            item=self.usb_device_pages.playlist_tree.currentItem()
            song=item.data(0,Qt.UserRole) if item else None
            if isinstance(song,CdjSong):
                row=self.usb_device_pages._display_rows.get(song.path,{})
                TrackInfoDialog(dict(row,location=str(self.usb_device_pages._snapshot.target)+song.path),parent=self).exec()
                return
        track=self._selected_track()
        if track is None and self.lib:
            track=self.lib.tracks_by_id.get(self.player_bar.current_track())
        if track is None:
            self._flash_status('Select a track to view its information');return
        tid=track.track_id
        dialog=TrackInfoDialog(self._row_for(track),artwork=lambda:self.art_cache.image_path(tid),
            pick_art=lambda:self._art_pick(tid),save=lambda changes:self._save_track_info(tid,changes),parent=self)
        dialog.exec()

    def _save_track_info(self,tid,changes):
        from copy import deepcopy
        from soundbox.track_info import validate
        from soundbox.paths import TITLE_MAX,ARTIST_MAX
        extra=validate({k:v for k,v in changes.items() if k not in ('title','artist')})
        for key,limit in (('title',TITLE_MAX),('artist',ARTIST_MAX)):
            if key in changes and (not changes[key].strip() or len(changes[key])>limit):
                raise ValueError(f'{key.title()} must contain 1–{limit} characters')
        before=(dict(self.titles),dict(self.overrides),deepcopy(self.settings.get('track_info',{})))
        if 'title' in changes:self.titles[tid]=changes['title']
        if 'artist' in changes:self.overrides[tid]=changes['artist']
        info=deepcopy(before[2]);info.setdefault(tid,{}).update(extra)
        self.settings['track_info']=info
        if not self._save_state():
            self.titles,self.overrides=before[:2];self.settings['track_info']=before[2];return False
        self.lib.info_overrides=info
        self._invalidate_rows([tid]);self._apply_and_refresh()
        self._flash_status('Track information saved in SoundBox')
        return True

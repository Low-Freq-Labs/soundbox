"""USB preference controls shared with SoundBox's global My Settings."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QFormLayout)
from .choices import ComboBox as QComboBox
from soundbox import my_settings

USB_TABS = ('Overview', 'Playlists', 'Category', 'Sort', 'Column', 'Color', 'My Settings')

GROUPS = (
    ('PLAYER · DJ SETTINGS', ('play_mode','eject_lock','needle_lock','quantize_beat_value',
        'hotcue_autoload','hotcue_color','auto_cue_level','time_mode','auto_cue','jog_mode',
        'tempo_range','master_tempo','quantize','sync','phase_meter','waveform',
        'waveform_divisions','vinyl_speed_adjust','beat_jump_beat_value')),
    ('PLAYER · DISPLAY', ('language','lcd_brightness','jog_lcd_brightness','jog_display_mode')),
    ('PLAYER · INDICATORS', ('slip_flashing','on_air_display','jog_ring_brightness',
        'jog_ring_indicator','disc_slot_illumination','pad_button_brightness')),
    ('MIXER · DJ SETTINGS', ('channel_fader_curve','channel_fader_curve_long','cross_fader_curve',
        'headphones_pre_eq','headphones_mono_split','headphones_mono_split_b','beat_fx_quantize','mic_low_cut','mic_echo_beats',
        'talk_over_mode','talk_over_level','midi_channel','midi_button_type')),
    ('MIXER · BRIGHTNESS', ('display_brightness','indicator_brightness')),
)
LABELS = {'play_mode':'Play mode / auto play mode', 'eject_lock':'Eject / load lock', 'hotcue_autoload':'Hot cue auto load',
    'hotcue_color':'Hot cue color', 'sync':'Sync (BPM / beat)', 'waveform':'Waveform / phase meter',
    'waveform_divisions':'Waveform divisions', 'channel_fader_curve_long':'Channel fader curve · long fader',
    'headphones_pre_eq':'Headphones · Pre EQ (A)', 'headphones_mono_split':'Headphones · Mono Split (A)',
    'headphones_mono_split_b':'Headphones · Mono Split (B)',
    'talk_over_mode':'Talkover mode', 'talk_over_level':'Talkover level',
    'lcd_brightness':'LCD brightness', 'jog_lcd_brightness':'Jog LCD brightness',
    'midi_channel':'MIDI channel', 'midi_button_type':'MIDI button type'}
NUMBERS = dict(zip(('one','two','three','four','five','six','seven','eight','nine','ten',
    'eleven','twelve','thirteen','fourteen','fifteen','sixteen','thirtytwo','sixtyfour'),
    ('1','2','3','4','5','6','7','8','9','10','11','12','13','14','15','16','32','64')))


def choice_label(key, token):
    if token=='minus_infinity':return '−∞'
    if key=='mic_echo_beats':return token+(' beat' if token=='1' else ' beats')
    if key=='play_mode':return {'continue_':'Continue / On','single':'Single / Off'}[token]
    if key=='mic_low_cut' and token=='on':return 'On (for MC)'
    if key == 'tempo_range':
        return {'six':'±6','ten':'±10','sixteen':'±16','wide':'Wide'}[token]
    if token.startswith('minus_'):
        return '−' + token[6:].replace('db',' dB')
    special = {'continue_':'Continue', 'memory':'Memory cue', 'hot_cue':'Hot cue', 'type1':'Type 1', 'type2':'Type 2',
        'half':'½', 'quarter':'¼', 'eighth':'⅛', 'dark':'1 · Dim', 'bright':'2 · Bright',
        'off':'Off', 'on':'On', 'cdj':'CDJ', 'pre_eq':'Pre EQ', 'post_eq':'Post EQ'}
    label = NUMBERS.get(token, special.get(token, token.replace('_',' ').capitalize()))
    if key in ('quantize_beat_value','beat_jump_beat_value'):
        label += ' beat' if token == 'one' else ' beats'
    return label


def section(title, parent_layout):
    label = QLabel(title)
    label.setObjectName('SettingsSection')
    parent_layout.addWidget(label)


class MySettingsEditor(QWidget):
    save_requested = Signal(dict)
    promote_requested = Signal(dict)
    load_requested = Signal()

    def __init__(self, *, global_page=False, parent=None):
        super().__init__(parent)
        self.global_page = global_page
        self.profile = {}
        self.controls = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.subtitle = QLabel('Defaults for newly formatted USBs' if global_page else 'Settings saved on this USB')
        self.subtitle.setObjectName('SettingsSub')
        self.subtitle.setContentsMargins(16,12,16,0)
        layout.addWidget(self.subtitle)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        host = QWidget(); columns = QHBoxLayout(host); columns.setContentsMargins(16,8,16,12); columns.setSpacing(32)
        left,right = QVBoxLayout(),QVBoxLayout()
        left.setSpacing(6);right.setSpacing(6)
        columns.addLayout(left,1); columns.addLayout(right,1)
        registry = my_settings.catalogue()['formats']
        for title, fields in GROUPS:
            column = left if title.startswith('PLAYER') else right
            section(title,column)
            for key in fields:
                filename = next(n for n,s in registry.items() if key in s['fields'])
                row = QHBoxLayout()
                row.setSpacing(12)
                label = QLabel(LABELS.get(key,key.replace('_',' ').capitalize()))
                row.addWidget(label,1)
                control = QComboBox(); control.setMinimumWidth(145); control.setMaximumWidth(220)
                control.setFixedHeight(28)
                for token in registry[filename]['fields'][key]['values']:
                    control.addItem(choice_label(key,token),token)
                control.currentIndexChanged.connect(self._changed)
                row.addWidget(control,1)
                column.addLayout(row)
                self.controls[filename,key] = control
            column.addSpacing(16)
        left.addStretch();right.addStretch()
        scroll.setWidget(host);layout.addWidget(scroll,1)
        actions = QHBoxLayout()
        actions.setContentsMargins(16,0,16,12)
        self.load = QPushButton('Load Global My Settings');self.load.clicked.connect(self.load_requested)
        self.promote = QPushButton('Save to Global My Settings')
        self.promote.clicked.connect(lambda:self.promote_requested.emit(self.current()))
        for button in (self.load,self.promote):
            button.setVisible(not global_page);actions.addWidget(button)
        actions.addStretch()
        self.save = QPushButton('Save Global My Settings' if global_page else 'Save to USB')
        self.save.clicked.connect(lambda:self.save_requested.emit(self.current() if self.global_page else self.delta()))
        actions.addWidget(self.save)
        layout.addLayout(actions)
        self.set_profile({})

    def set_profile(self, profile, global_profile=None, *, preserve_edits=False):
        pending=self.delta() if preserve_edits else {}
        self.profile = deepcopy(profile)
        self.global_profile = deepcopy(global_profile or {})
        for (name,key), control in self.controls.items():
            value = profile.get(name,{}).get(key)
            if value is not None:value=pending.get(name,{}).get(key,value)
            control.blockSignals(True)
            unknown = control.findData(None)
            if unknown >= 0:control.removeItem(unknown)
            index = control.findData(value)
            if index < 0:
                control.addItem('Unavailable',None);index = control.count()-1
            control.setCurrentIndex(index);control.setEnabled(value is not None)
            control.blockSignals(False)
        self._changed()

    def current(self):
        result = deepcopy(self.profile)
        for (name,key),control in self.controls.items():
            if control.isEnabled() and control.currentData() is not None:
                result.setdefault(name,{})[key] = control.currentData()
        return result

    def _changed(self, *_):
        current = self.current()
        self.save.setEnabled(bool(current) and current != self.profile)
        differs = any(value != self.global_profile.get(name,{}).get(key)
                      for name,fields in current.items() for key,value in fields.items())
        self.promote.setVisible(not self.global_page and bool(current) and differs)
        self.promote.setEnabled(current==self.profile)
        self.promote.setToolTip('Save USB changes first' if current!=self.profile else '')
        self.load.setEnabled(bool(self.profile) and bool(self.global_profile) and differs)

    def delta(self):
        return {name:{key:value for key,value in fields.items() if value!=self.profile.get(name,{}).get(key)}
                for name,fields in self.current().items() if fields!=self.profile.get(name,{})}


class OrderedChoices(QWidget):
    save_requested = Signal(list)

    def __init__(self, label, mandatory, parent=None):
        super().__init__(parent)
        self.mandatory = set(mandatory)
        self.baseline = []
        layout = QVBoxLayout(self);layout.setContentsMargins(20,16,20,16)
        lists = QHBoxLayout();self.inactive,self.active = QListWidget(),QListWidget()
        self.active.setDragDropMode(QAbstractItemView.InternalMove)
        self.active.model().rowsMoved.connect(self._changed)
        for title,view in ((f'Inactive {label}',self.inactive),(f'Active {label}',self.active)):
            view.setObjectName('UsbChoices')
            col = QVBoxLayout();section(title.upper(),col);col.addWidget(view)
            lists.addLayout(col,1)
            if view is self.inactive:
                arrows = QVBoxLayout();arrows.addStretch()
                for title,source,target in (('→',self.inactive,self.active),('←',self.active,self.inactive)):
                    button=QPushButton(title);button.setFixedWidth(42)
                    button.clicked.connect(lambda _=False,s=source,t=target:self._move(s,t))
                    arrows.addWidget(button)
                arrows.addStretch();lists.addLayout(arrows)
        layout.addLayout(lists,1)
        buttons = QHBoxLayout();buttons.addStretch()
        for title,delta in (('Up',-1),('Down',1)):
            button=QPushButton(title);button.clicked.connect(lambda _=False,d=delta:self._reorder(d));buttons.addWidget(button)
        self.save=QPushButton('Save to USB');self.save.clicked.connect(lambda:self.save_requested.emit(self.order()))
        buttons.addWidget(self.save);layout.addLayout(buttons)
        self.set_slots([])

    def set_slots(self, slots):
        if getattr(self,'_slots',None)==slots:return
        self._slots=deepcopy(slots)
        self.active.clear();self.inactive.clear()
        for _id,menu,name,seq,visible in sorted(slots,key=lambda s:(not s[4],s[3] if s[4] else s[2])):
            item=QListWidgetItem(name.title() if name != 'BPM' else 'BPM')
            item.setData(Qt.UserRole,menu)
            if menu in self.mandatory:item.setToolTip('Always available on the player')
            (self.active if visible else self.inactive).addItem(item)
        self.baseline=self.order();self._changed()

    def order(self):
        return [self.active.item(i).data(Qt.UserRole) for i in range(self.active.count())]

    def _move(self,source,target):
        item=source.currentItem()
        if item is None or (source is self.active and item.data(Qt.UserRole) in self.mandatory):return
        target.addItem(source.takeItem(source.row(item)));self._changed()

    def _reorder(self,delta):
        row=self.active.currentRow();new=row+delta
        if row>=0 and 0<=new<self.active.count():
            item=self.active.takeItem(row);self.active.insertItem(new,item);self.active.setCurrentRow(new);self._changed()

    def _changed(self,*_):
        self.save.setEnabled(self.order()!=self.baseline)


class BrowsePreferences(QWidget):
    save_requested = Signal(dict)

    def __init__(self, kind, parent=None):
        super().__init__(parent);self.kind=kind;self.controls={};self.baseline={}
        layout=QVBoxLayout(self);layout.setContentsMargins(20,16,20,16)
        section('DEFAULT RIGHT COLUMN' if kind=='column' else 'COLOR COMMENTS',layout)
        if kind=='column':
            text=QLabel('Shown beside the track name on CDJ / XDJ');text.setObjectName('SettingsSub');layout.addWidget(text)
            self.column=QComboBox();self.column.setMaximumWidth(420);layout.addWidget(self.column)
            self.column.currentIndexChanged.connect(self._changed)
        else:
            grid=QGridLayout();grid.setHorizontalSpacing(8);grid.setVerticalSpacing(10)
            for at,(name,color) in enumerate(zip(('Pink','Red','Orange','Yellow','Green','Aqua','Blue','Purple'),
                    ('#ed65dc','#f63324','#f5a43a','#ede63a','#54cf31','#40bae5','#204be9','#8e19dd'))):
                label=QLabel('●');label.setFixedWidth(16);label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f'color: {color}');label.setAccessibleName(name)
                edit=QLineEdit();edit.setMaxLength(128);edit.textEdited.connect(self._changed)
                edit.setAccessibleName(name+' comment')
                grid.addWidget(label,at,0);grid.addWidget(edit,at,1);self.controls[str(at+1)]=edit
            grid.setColumnStretch(1,1);layout.addLayout(grid)
        layout.addStretch();actions=QHBoxLayout();actions.addStretch()
        self.save=QPushButton('Save to USB');self.save.setEnabled(False)
        self.save.clicked.connect(lambda:self.save_requested.emit(self.changes()))
        actions.addWidget(self.save);layout.addLayout(actions)

    def set_preferences(self,preferences):
        incoming=preferences.get('colors',[]) if self.kind=='colors' else (
            preferences.get('sorts',[]),preferences.get('sub_column'))
        if getattr(self,'_incoming',None)==incoming:return
        self._incoming=deepcopy(incoming)
        if self.kind=='column':
            self.column.blockSignals(True);self.column.clear();self.column.addItem('Not specified',None)
            for _id,menu,name,_seq,_visible in preferences.get('sorts',[]):
                if menu not in (25,26):self.column.addItem(name.title(),menu)
            self.column.setCurrentIndex(max(0,self.column.findData(preferences.get('sub_column'))))
            self.column.blockSignals(False)
        else:
            colors=dict((str(i),name) for i,name in preferences.get('colors',[]))
            for key,edit in self.controls.items():
                edit.setText(colors.get(key,''));edit.setEnabled(key in colors)
        self.setEnabled(bool(preferences.get('sorts') if self.kind=='column' else preferences.get('colors')))
        self.baseline=self.current();self._changed()

    def current(self):
        return {'sub_column':self.column.currentData()} if self.kind=='column' else {
            'colors':{key:edit.text() for key,edit in self.controls.items() if edit.isEnabled()}}

    def changes(self):
        current=self.current()
        if self.kind=='column':return current
        return {'colors':{key:value for key,value in current['colors'].items()
                          if value!=self.baseline.get('colors',{}).get(key)}}

    def _changed(self,*_):
        self.save.setEnabled(self.current()!=self.baseline)


class FormatUsbDialog(QDialog):
    def __init__(self, target, parent=None):
        super().__init__(parent)
        from pathlib import Path
        from soundbox import usb_format
        from .devices import human
        self.choice=None
        self.setWindowTitle('Reformat USB');self.setMinimumWidth(460)
        layout=QVBoxLayout(self)
        text=QLabel(f'{Path(target.target).name} · {human(target.capacity)}\n\n'
            'Reformatting permanently erases every file on this USB.\n'+ '\n'.join(target.volumes))
        text.setWordWrap(True);layout.addWidget(text)
        form=QFormLayout();self.name=QLineEdit(Path(target.target).name);self.filesystem=QComboBox()
        for value,label in usb_format.FORMATS:self.filesystem.addItem(label,value)
        form.addRow('USB name',self.name);form.addRow('Format',self.filesystem);layout.addLayout(form)
        note=QLabel('FAT32 works with older players. exFAT requires a compatible newer player.')
        note.setWordWrap(True);note.setObjectName('SettingsSub');layout.addWidget(note)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.Cancel)
        erase=buttons.addButton('Reformat USB',QDialogButtonBox.DestructiveRole)
        buttons.rejected.connect(self.reject)
        def confirm():
            try:name=usb_format.name_for(self.name.text(),self.filesystem.currentData())
            except ValueError as exc:self.error.setText(str(exc));return
            self.choice=(name,self.filesystem.currentData());self.accept()
        erase.clicked.connect(confirm);layout.addWidget(buttons)

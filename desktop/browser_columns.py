"""Shared browser defaults and exact-playlist column layouts, before reveal.

Qt header state retains hidden widths; stable column keys detach that state from
logical indexes added or reordered in later builds. This is display preference
state only and never changes musical order or native source authority.
"""
from copy import deepcopy
from functools import lru_cache
import json
from uuid import UUID,uuid4

from PySide6.QtCore import QByteArray,QEvent,QObject,Qt,QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QHeaderView

from .model import COLUMNS

KEY='browser_column_layouts'


def source(library):
    return (library.source_kind,str(library.source),library.source_library_id) if library else ('','','')


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def state(window):
    value=window.settings.get(KEY)
    return value if isinstance(value,dict) and value.get('version')==1 else {}


def capture(header):
    return dict(state=bytes(header.saveState().toBase64()).decode('ascii'),keys=[key for key,_label,_hidden in COLUMNS])


@lru_cache(maxsize=128)
def _decode(blob,keys):
    header=QHeaderView(Qt.Horizontal)
    model=QStandardItemModel(0,len(keys),header);header.setModel(model)
    try:
        raw=QByteArray.fromBase64(blob.encode('ascii'),QByteArray.AbortOnBase64DecodingErrors)
        if not raw or not header.restoreState(raw):return None
        rows=[]
        for visual in range(header.count()):
            logical=header.logicalIndex(visual)
            if not 0<=logical<len(keys):return None
            hidden=header.isSectionHidden(logical)
            if hidden:header.showSection(logical)  # Unshown decoder only; retain Qt's saved hidden width.
            rows.append((keys[logical],header.sectionSize(logical),hidden))
        return tuple(rows)
    except (ValueError,UnicodeError,RuntimeError):return None
    finally:header.deleteLater()


def decoded(layout):
    if (not isinstance(layout,dict) or set(layout)!={'state','keys'}
            or not isinstance(layout['state'],str) or not isinstance(layout['keys'],list)
            or not layout['keys'] or any(type(key) is not str or not key for key in layout['keys'])
            or len(set(layout['keys']))!=len(layout['keys'])):return None
    return _decode(layout['state'],tuple(layout['keys']))


def local_changes(window,previous,current,rename=None):
    """Stage only this source partition's rename/deletion with its musical save."""
    value=state(window)
    if not value:return None
    namespace=encoded(source(window.lib));maps=value.get('local_ids',{})
    if not isinstance(maps,dict) or not isinstance(maps.get(namespace),dict):return None
    result=deepcopy(value);ids=result['local_ids'][namespace];layouts=result.setdefault('playlists',{})
    if not isinstance(layouts,dict):return None
    if rename and rename[0] in ids:
        token=ids.pop(rename[0]);ids[rename[1]]=token
    for name in set(previous)-set(current):
        token=ids.pop(name,None)
        if token:layouts.pop(encoded(['local',*source(window.lib),token]),None)
    return result if result!=value else None


class BrowserColumns(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.applying=False;self.scope=None
        self.view='sb:Collection';self.gesture=None;self.initialized=False
        current=capture(window.table.horizontalHeader())
        legacy=dict(state=window.settings.get('columns_state',''),
                    keys=window.settings.get('columns_keys',[key for key,_label,_hidden in COLUMNS[:10]]))
        saved=state(window).get('default')
        self.default=deepcopy(saved if decoded(saved) is not None else legacy if decoded(legacy) is not None else current)
        for header in (window.table.horizontalHeader(),window.artists_view.header()):
            header.setSectionsMovable(True);header.setFirstSectionMovable(True)
            header.viewport().installEventFilter(self)
        self.activate('sb:Collection',force=True)

    def binding(self):
        w=self.window;repo=w._state_repository
        identity=self.resolve(self.view)
        if identity is not None and identity.startswith('["local-unset"'):
            identity=(self.view,id(w.custom.get(self.view[3:])))
        return source(w.lib),repo,repo.generation,repo.area.generation,identity

    def resolve(self,view,*,create=False):
        w=self.window;lib=w.lib
        if lib is None or view in ('sb:Collection','sb:Artists'):return None
        if view.startswith('sb:'):
            name=view[3:]
            if name not in w.custom:return None
            controller=getattr(w,'unification_controller',None)
            merged=controller.state() if controller is not None else None
            if merged and merged.get('customs_done'):
                row=merged['nodes'].get('custom:'+name)
                if row:return self.native(row['id'],row['uuid'])
            namespace=encoded(source(lib));value=state(w);maps=value.get('local_ids',{})
            ids=maps.get(namespace,{}) if isinstance(maps,dict) else {}
            token=ids.get(name) if isinstance(ids,dict) else None
            try:
                if str(UUID(token))!=token:token=None
            except (ValueError,TypeError,AttributeError):token=None
            if token is None and create:
                value=deepcopy(value) if value else dict(version=1)
                maps=value.setdefault('local_ids',{})
                if not isinstance(maps,dict):maps={};value['local_ids']=maps
                ids=maps.setdefault(namespace,{})
                if not isinstance(ids,dict):ids={};maps[namespace]=ids
                token=uuid4().hex;ids[name]=str(UUID(token));token=ids[name]
                w.settings[KEY]=value
            return encoded(['local',*source(lib),token]) if token else encoded(['local-unset',*source(lib),name])
        if view.startswith('rb:'):
            path=view[3:];presentation=w._native_presentation()
            if lib.source_kind=='rekordbox-db':
                # Sidebar items carry the exact native identity even when two
                # playlists have the same display path. Folder navigation keeps
                # the active playlist; look it up rather than using currentItem.
                from .trees import NATIVE_BINDING_ROLE,PATH_ROLE
                identity=getattr(w,'_active_native_playlist_id',None)
                item=w._find_tree_item('',identity) if identity else None
                binding=item.data(0,NATIVE_BINDING_ROLE) if item is not None else None
                if binding and item.data(0,PATH_ROLE)==path:
                    if (binding.get('source'),binding.get('library_id'))!=(str(lib.source),lib.source_library_id):return None
                    if binding.get('id')!=identity or not binding.get('uuid'):return None
                    return self.native(binding.get('id'),binding.get('uuid'))
            selected=[p for p in presentation.playlists if p.display_path==path]
            if len(selected)!=1:return None
            playlist=selected[0]
            if lib.source_kind=='rekordbox-db':return self.native(playlist.pid)
            # XML provides no native UUID; its unambiguous full path is the available identity.
            return encoded(['xml',*source(lib),*playlist.folder_path,playlist.name])
        return None

    def native(self,identity,uuid=None):
        lib=self.window.lib
        rows=[row for row in self.window._native_presentation().source_playlist_catalog.get('nodes',())
              if row.get('ID')==identity and row.get('rb_local_deleted')==0 and row.get('Attribute') in (0,4)]
        if len(rows)!=1 or not rows[0].get('UUID') or uuid is not None and rows[0]['UUID']!=uuid:return None
        return encoded(['native',*source(lib),identity,rows[0]['UUID']])

    def structure(self):
        header=self.window.table.horizontalHeader()
        return tuple((header.logicalIndex(i),header.sectionSize(header.logicalIndex(i)),
                      header.isSectionHidden(header.logicalIndex(i))) for i in range(header.count()))

    def has_saved_widths(self):
        value=state(self.window);layouts=value.get('playlists',{})
        selected=layouts.get(self.scope) if isinstance(layouts,dict) and self.scope else None
        legacy=dict(state=self.window.settings.get('columns_state',''),
                    keys=self.window.settings.get('columns_keys',[key for key,_label,_hidden in COLUMNS[:10]]))
        return any(decoded(layout) is not None for layout in (selected,value.get('default'),legacy))

    def eventFilter(self,obj,event):
        kind=event.type()
        if kind in (QEvent.MouseButtonPress,QEvent.MouseButtonDblClick) and event.button()==Qt.LeftButton:
            self.gesture=(self.binding(),self.structure())
        elif kind==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton:
            gesture=self.gesture
            QTimer.singleShot(0,self,lambda:self.finish(gesture))
        return False

    def finish(self,gesture):
        if gesture is None or self.gesture is not gesture:return
        self.gesture=None;w=self.window
        if w._shutting_down or w._state_maintenance:return
        if self.binding()!=gesture[0]:
            self.activate(self.view,force=True);return
        if self.structure()!=gesture[1]:self.remember(customize=True)

    def activate(self,view,*,force=False):
        if self.applying:return
        scope=self.resolve(view)
        if not force and scope==self.scope and (scope is not None or
                view in ('sb:Collection','sb:Artists') and self.view in ('sb:Collection','sb:Artists')):
            self.view=view;return
        if self.initialized and not force and self.scope is None and self.view in ('sb:Collection','sb:Artists'):
            self.default=capture(self.window.table.horizontalHeader())
        self.scope=scope;self.view=view
        layouts=state(self.window).get('playlists',{})
        saved=layouts.get(scope) if isinstance(layouts,dict) and scope else None
        self.apply(saved if decoded(saved) is not None else self.default)
        self.initialized=True

    def apply(self,layout):
        rows=decoded(layout)
        if rows is None:return
        w=self.window;header=w.table.horizontalHeader();self.applying=True
        blocked=header.blockSignals(True)
        try:
            for target in (header,w.artists_view.header()):
                target_blocked=target.blockSignals(True)
                try:
                    # Older Qt blobs can carry Stretch modes. Layout preferences
                    # own widths; the shared bounded fitter owns window growth.
                    target.setStretchLastSection(False)
                    target.setSectionResizeMode(QHeaderView.Interactive)
                finally:target.blockSignals(target_blocked)
            indexes={key:i for i,(key,_label,_hidden) in enumerate(COLUMNS)}
            for key,width,hidden in rows:
                if key in indexes:
                    logical=indexes[key];header.resizeSection(logical,width);header.setSectionHidden(logical,hidden)
            known={key for key,_width,_hidden in rows}
            for logical,(key,_label,hidden) in enumerate(COLUMNS):
                if key not in known:header.setSectionHidden(logical,hidden)
            order=[indexes[key] for key,_width,_hidden in rows if key in indexes]
            order.extend(index for index in range(len(COLUMNS)) if index not in order)
            for visual,logical in enumerate(order):header.moveSection(header.visualIndex(logical),visual)
            header.setSectionsMovable(True);header.setFirstSectionMovable(True);header.setSectionsClickable(True)
            w._fit_browser_columns();w._sync_artist_columns()
        finally:
            header.blockSignals(blocked);self.applying=False

    def remember(self,*,customize=False):
        if self.applying:return
        w=self.window;value=deepcopy(state(w)) or dict(version=1)
        layouts=value.get('playlists',{})
        if self.scope is not None and not customize and (not isinstance(layouts,dict) or self.scope not in layouts):return
        if not customize:
            saved=value.get('default') if self.scope is None else layouts.get(self.scope)
            if decoded(saved) is None:
                if self.scope is None:self.default=capture(w.table.horizontalHeader())
                return  # Merely closing/refreshing never replaces unsupported saved preference data.
        if customize:
            self.scope=self.resolve(self.view,create=True)
            if self.scope is None and self.view not in ('sb:Collection','sb:Artists'):return
            value=deepcopy(state(w)) or dict(version=1)
        layout=capture(w.table.horizontalHeader())
        if self.scope is None:
            self.default=layout;value['default']=layout
            w.settings['columns_state']=layout['state'];w.settings['columns_keys']=layout['keys']
            w._schedule_settings_save('columns_state','columns_keys')
        else:
            layouts=value.setdefault('playlists',{})
            if not isinstance(layouts,dict):layouts={};value['playlists']=layouts
            layouts[self.scope]=layout
        w.settings[KEY]=value;w._schedule_settings_save(KEY)

    def visibility(self,column,hidden,*,expected=None):
        if expected is not None and self.binding()!=expected:return
        self.applying=True
        try:
            self.window.table.setColumnHidden(column,hidden)
            self.window._fit_browser_columns(fill=True);self.window._sync_artist_columns()
        finally:self.applying=False
        self.remember(customize=True)

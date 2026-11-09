"""Shared synthetic encrypted native-library builders for disposable workspaces.

These helpers create fixtures only; they provision no source-write authority.
"""
import json

from research.native_library import registration as reg
from soundbox.rekordbox_write import _connect, _state, _LINK_COLUMNS

_DEVICE = '00000000-0000-4000-8000-000000000003'


def fixture(root):
    conn = _connect(root/'master.db', create=True)
    try:
        defaults = reg._DEFAULTS
        fields = [name+' '+('INTEGER' if type(defaults.get(name)) is int or name in
                  ('Length', 'FileType', 'FileSize', 'rb_local_usn') else
                  'REAL' if name == 'SamplerGain' else 'TEXT')+(' PRIMARY KEY' if name=='ID' else '')
                  for name in sorted(reg._FIELDS)]
        conn.execute('CREATE TABLE djmdContent ('+','.join(fields)+')')
        conn.executescript(f'''
            CREATE TABLE djmdProperty(DBID TEXT PRIMARY KEY, DBVersion TEXT, DeviceID TEXT);
            INSERT INTO djmdProperty VALUES ('123','6000','{_DEVICE}');
            CREATE TABLE agentRegistry(registry_id TEXT PRIMARY KEY,int_1 INTEGER,updated_at TEXT,opaque BLOB);
            INSERT INTO agentRegistry VALUES ('localUpdateCount',40,'counter-time',X'00ff');
            INSERT INTO agentRegistry VALUES ('notificationNextFetch',77,'notification-time',X'0011');
            CREATE TABLE contentFile(ID TEXT PRIMARY KEY,ContentID TEXT,Hash TEXT);
            CREATE TABLE djmdPlaylist(ID TEXT PRIMARY KEY,Name TEXT);
            INSERT INTO djmdPlaylist VALUES ('playlist','keep');
            CREATE TABLE djmdSongPlaylist(ID TEXT PRIMARY KEY,ContentID TEXT,PlaylistID TEXT);
            CREATE TABLE unknown_table(ID TEXT PRIMARY KEY,opaque BLOB);
            INSERT INTO unknown_table VALUES ('opaque',X'ff0011');
        ''')
        conn.execute("INSERT INTO djmdContent(ID,Title,FolderPath,UUID,rb_local_deleted) VALUES ('1','Existing','/never/open.wav','old',0)")
        state = _state(conn)
    finally:
        conn.close()
    (root/'state.json').write_text(json.dumps(state))
    (root/'masterPlaylists6.xml').write_bytes(b'<opaque native="preserve"/>')


def inventory(path):
    conn = _connect(path, readonly=True)
    try:
        return _state(conn)
    finally:
        conn.close()


def source_fixture(root):
    root.mkdir(parents=True)
    fixture(root)
    conn=_connect(root/'master.db')
    try:
        conn.execute('CREATE TABLE djmdSongMyTag ('+','.join('"'+c+'" TEXT' for c in _LINK_COLUMNS)+')')
        conn.execute('CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER)')
        state=_state(conn)
    finally:conn.close()
    (root/'state.json').write_text(json.dumps(state))

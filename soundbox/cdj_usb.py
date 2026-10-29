"""Dedicated CDJ USB boundary: bounded metadata capture and local rendering.

Ordinary stock/Rockbox sessions retain their PIONEER exclusion. This module is
admitted only for an explicitly selected generic USB, never an iPod/Rockbox.
Both library formats, shared payloads and display settings form one generation.
"""
from __future__ import annotations

import binascii
from dataclasses import dataclass, field as dataclass_field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile

PROFILE_FILES = ('DEVSETTING.DAT', 'MYSETTING.DAT', 'MYSETTING2.DAT',
                 'DJMMYSETTING.DAT', 'djprofile.nxs')
DATABASE_FILES = tuple('PIONEER/rekordbox/' + name for name in
                       ('export.pdb', 'exportExt.pdb', 'exportLibrary.db'))
DISPLAY_FIELDS = {
    'waveform_color': (114, (('BLUE', 1), ('RGB', 3), ('3Band', 4))),
    'waveform_position': (117, (('LEFT', 2), ('CENTER', 1))),
    'overview_waveform': (113, (('Half Waveform', 1), ('Full Waveform', 2))),
    'key_format': (116, (('Classic', 1), ('Alphanumeric', 2))),
}


def renderer_path():
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS')) / 'native/soundbox-cdj'
    return Path(__file__).resolve().parents[1] / 'build/cdj/release/soundbox-cdj'


def run_renderer(command, workspace, *args, should_cancel=None):
    workspace = Path(workspace).resolve(strict=True)
    args = tuple(Path(arg).resolve(strict=False) for arg in args)
    binary = renderer_path()
    if not binary.is_file():
        raise RuntimeError('CDJ renderer is missing from this build')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(binary), command, str(workspace), *map(str, args)],
                                   stdout=output, stderr=errors)
        try:
            while True:
                try:
                    status = process.wait(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    if should_cancel and should_cancel():
                        process.terminate()
                        raise RuntimeError('CDJ preparation cancelled')
            output.seek(0)
            errors.seek(0)
            if status:
                raise RuntimeError(errors.read(16_384).decode('utf-8', errors='replace').strip())
            return json.loads(output.read(32 * 1024 * 1024))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class CdjReadSession:
    """Pinned descriptors; exact paths only, no device directory traversal."""
    def __init__(self, target, token):
        self.target = Path(target)
        self.fd: int | None = os.open(self.target, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        self.initial = os.fstat(self.fd)
        if token <= 0 or self.initial.st_dev != token:
            self.close()
            raise ValueError('USB mount identity changed')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def revalidate(self):
        now = os.stat(self.target, follow_symlinks=False)
        if (now.st_dev, now.st_ino) != (self.initial.st_dev, self.initial.st_ino):
            raise ValueError('USB was remounted during the read')

    def read(self, relative, *, limit=64 * 1024 * 1024):
        parts = PurePosixPath(relative).parts
        if not parts or any(p in ('.', '..', '/') for p in parts):
            raise ValueError('Invalid CDJ metadata path')
        if self.fd is None:
            raise ValueError('CDJ read session is closed')
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != self.initial.st_dev:
                    raise ValueError('CDJ metadata crosses the USB boundary')
            child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, 'rb') as source:
                before = os.fstat(source.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_dev != self.initial.st_dev
                        or before.st_size > limit):
                    raise ValueError('Unsupported CDJ metadata file')
                raw = source.read(limit + 1)
                after = os.fstat(source.fileno())
            path_after = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if len(raw) != before.st_size or _identity(before) != _identity(after) or _identity(before) != _identity(path_after):
                raise ValueError('CDJ metadata changed during capture')
            self.revalidate()
            return raw
        finally:
            os.close(fd)

    def stamp(self, relative):
        parts=PurePosixPath(relative).parts
        if not parts or any(p in ('.','..','/') for p in parts) or self.fd is None:
            raise ValueError('Invalid CDJ metadata path')
        fd=os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY,dir_fd=fd)
                os.close(fd);fd=child
                if os.fstat(fd).st_dev!=self.initial.st_dev:raise ValueError('CDJ metadata crosses the USB boundary')
            info=os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_dev!=self.initial.st_dev:raise ValueError('Unsupported CDJ metadata path')
            self.revalidate();return _identity(info)
        finally:os.close(fd)


def metadata_stamp(target,token):
    """Eight small catalog/profile entries, never audio or directory inventories."""
    session=CdjReadSession(target,token)
    try:
        result=[]
        for path in (*DATABASE_FILES,*('PIONEER/'+name for name in PROFILE_FILES),
                     DATABASE_FILES[2]+'-wal',DATABASE_FILES[2]+'-journal','.soundbox-cdj/pending.json'):
            try:value=session.stamp(path)
            except FileNotFoundError:value=None
            result.append((path,value))
        return tuple(result)
    finally:session.close()


@dataclass(frozen=True)
class CdjPlaylist:
    id: str
    parent: str
    title: str
    folder: bool
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CdjSong:
    id: str
    path: str
    title: str
    artist: str = ''
    bpm: int = 0
    key: str = ''
    duration: int = 0
    bitrate: int = 0
    genre: str = ''
    date_added: str = ''
    analysis: str = ''
    artwork: str = ''
    source_id: str = ''
    source_db_id: str = ''
    info: dict = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class CdjLibrary:
    playlists: tuple[CdjPlaylist, ...]
    name: str
    background_plus: int | None
    background_classic: int | None
    display: tuple[tuple[str, str], ...]
    tag_master_id: int
    # Bytes are a presentation baseline, never confirmation/write authority.
    files: tuple[tuple[str, bytes], ...]
    settings_signature: str = ""
    managed: bool = False
    source_db_ids: tuple[int, ...] = ()
    songs: tuple[CdjSong, ...] = ()
    preferences: dict = dataclass_field(default_factory=dict)
    catalog: dict = dataclass_field(default_factory=dict)


def display_values(raw):
    if (len(raw) != 140 or raw[:4] != b'\x60\0\0\0'
            or raw[4:36].rstrip(b'\0') != b'PIONEER DJ'
            or raw[36:68].rstrip(b'\0') != b'rekordbox'
            or raw[68:100].rstrip(b'\0') not in (b'6.8.7',b'7.2.11')
            or raw[104:113] != bytes.fromhex('785634120100000001')
            or raw[100:104] != b'\x20\0\0\0'
            or int.from_bytes(raw[136:138], 'little') != binascii.crc_hqx(raw[104:136], 0)):
        raise ValueError('Unrecognized DEVSETTING framing or checksum')
    result = {}
    for field, (offset, options) in DISPLAY_FIELDS.items():
        result[field] = dict((value, name) for name, value in options).get(raw[offset], 'Unsupported')
    return result


def patch_display(raw, changes):
    values = display_values(raw)
    output = bytearray(raw)
    for field, name in changes.items():
        if field not in DISPLAY_FIELDS or values[field] == 'Unsupported':
            raise ValueError('Unsupported display setting; existing bytes must be preserved')
        offset, options = DISPLAY_FIELDS[field]
        if name not in dict(options):
            raise ValueError('Unsupported display choice')
        output[offset] = dict(options)[name]
    output[136:138] = binascii.crc_hqx(output[104:136], 0).to_bytes(2, 'little')
    return bytes(output)


def read_library(target, token, *, allow_pending=False, check_pair=False, snapshot_only=False):
    """Copy only native databases/profiles; parse local copies, never live SQL."""
    if not snapshot_only:assert_no_database_journals(target,token)
    session = CdjReadSession(target, token)
    try:
        try:
            session.read('.soundbox-cdj/pending.json', limit=8*1024*1024)
        except FileNotFoundError:
            pass
        else:
            if not allow_pending:
                raise ValueError('USB update interrupted — choose Update Playlists to continue')
        try:
            managed = json.loads(session.read('.soundbox-cdj/manifest.json', limit=8*1024*1024)).get('library_managed', True)
        except FileNotFoundError:
            managed = False
        files = {}
        for path in DATABASE_FILES:
            try:
                files[path] = session.read(path)
            except FileNotFoundError:
                pass
        companions={}
        if snapshot_only:
            try:
                journal=session.read(DATABASE_FILES[2]+'-journal')
            except FileNotFoundError:journal=b''
            if journal:raise ValueError('USB library is updating; reading it again shortly')
            try:companions[DATABASE_FILES[2]+'-wal']=session.read(DATABASE_FILES[2]+'-wal')
            except FileNotFoundError:pass
        if DATABASE_FILES[0] not in files and DATABASE_FILES[2] not in files:
            return None
        from .cdj_pdb import audit
        for path in DATABASE_FILES[:2]:
            if path in files:
                issues = audit(files[path])['issues']
                if issues:
                    raise ValueError('Invalid native page/index structure: '+str(issues[:3]))
        for name in PROFILE_FILES:
            try:
                files['PIONEER/' + name] = session.read('PIONEER/' + name, limit=64 * 1024)
            except FileNotFoundError:
                pass
        # Recheck every file after the complete capture, including deck profile saves.
        for path, raw in (files|companions).items():
            if session.read(path, limit=max(len(raw), 64 * 1024)) != raw:
                raise ValueError('USB library changed during capture; retry after rekordbox finishes')
        with tempfile.TemporaryDirectory(prefix='soundbox-cdj-view-') as directory:
            root = Path(directory)
            for path, raw in (files|companions).items():
                local = root/path
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(raw)
            result = run_renderer('inspect', root)
            if check_pair:run_renderer('catalog-check',root)
        display = {}
        if 'PIONEER/DEVSETTING.DAT' in files:
            try:
                display = display_values(files['PIONEER/DEVSETTING.DAT'])
            except ValueError:
                display = dict.fromkeys(DISPLAY_FIELDS,'Unsupported')
        playlists = tuple(CdjPlaylist(str(row['id']), str(row['parent']), row['title'], row['folder'],
                          tuple(tuple(entry) for entry in row['entries'])) for row in result['playlists'])
        by_id = {p.id:p for p in playlists}
        if len(by_id) != len(playlists):
            raise ValueError('Duplicate native playlist identity')
        for playlist in playlists:
            parent, seen = playlist.parent, {playlist.id}
            while parent != '0':
                if parent in seen or parent not in by_id or not by_id[parent].folder or len(seen)>64:
                    raise ValueError('Invalid native playlist hierarchy')
                seen.add(parent)
                parent = by_id[parent].parent
        return CdjLibrary(playlists, result['name'], result['background_plus'],
                          result['background_classic'], tuple(display.items()),
                          result.get('tag_master_id', 0), tuple(files.items()),
                          digest(json.dumps(result['preferences'], sort_keys=True, separators=(',', ':')).encode()), managed,
                          tuple(sorted({int(t['master_db_id']) for t in result['track_rows']})),
                          tuple(CdjSong(str(t['id']),t['audio'],t['title'],t['artist'],int(t['bpm']),t['key'],
                              int(t['duration']),int(t['bitrate']),t['genre'],t['date_added'],t['analysis'],
                              t['artwork'],str(t['source_id']),str(t['master_db_id']),t.get('info',{})) for t in result['track_rows']),
                          result.get('preferences', {}),result)
    finally:
        session.close()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def default_display():
    """Explicit new-USB factory profile, independent of desktop preferences."""
    import struct
    raw = bytearray(140)
    struct.pack_into('<I', raw, 0, 96)
    for offset, value in ((4, 'PIONEER DJ'), (36, 'rekordbox'), (68, '6.8.7')):
        raw[offset:offset+32] = value.encode().ljust(32, b'\0')
    struct.pack_into('<I', raw, 100, 32)
    raw[104:136] = bytes.fromhex('7856341201000000010103010202000000000000000000000000000000000000')
    raw[136:138] = binascii.crc_hqx(raw[104:136], 0).to_bytes(2, 'little')
    return bytes(raw)


def read_profiles(target, token):
    session = CdjReadSession(target,token)
    try:
        profiles = []
        for name in PROFILE_FILES:
            try:
                raw = session.read('PIONEER/'+name,limit=64*1024)
            except FileNotFoundError:
                continue
            if session.read('PIONEER/'+name,limit=64*1024) != raw:
                raise ValueError('USB settings changed during capture')
            profiles.append((name,raw))
        return profiles
    finally:
        session.close()


def assert_no_database_journals(target, token):
    """A USB DB with live/unreconciled SQLite companions cannot be replaced."""
    session = CdjReadSession(target,token)
    try:
        for suffix in ('-wal','-shm','-journal'):
            try:
                session.read(DATABASE_FILES[2]+suffix,limit=0)
            except FileNotFoundError:
                continue
            except (OSError,ValueError):
                raise ValueError('USB database has active or unreconciled SQLite files; finish/close rekordbox before retrying') from None
            raise ValueError('USB database has active or unreconciled SQLite files; finish/close rekordbox before retrying')
    finally:
        session.close()


def prepare_playback(target, token, song):
    """Validate the selected USB reference without opening its audio on the UI thread."""
    # The caller already carries the verified volume identity separately. This
    # read-only helper uses the snapshot mount and no write/publication API.
    session = CdjReadSession(target,token)
    try:
        def checked(relative):
            parts = PurePosixPath(relative.lstrip('/')).parts
            if not parts or any(p in ('.','..','/') or ':' in p or '\\' in p or '\0' in p for p in parts):
                raise ValueError('Invalid USB playback reference')
            path = Path(target).joinpath(*parts)
            parent = Path(target)
            for part in parts:
                parent = parent/part
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_dev != token:
                    raise ValueError('USB playback reference is linked or crosses the mount')
            session.revalidate()
            if not path.is_file():
                raise ValueError('USB playback file is unavailable')
            return path
        audio = checked(song.path)
        analysis = checked(song.analysis) if song.analysis else None
        if analysis is not None:
            for extension in ('EXT','2EX'):
                sibling = analysis.with_suffix('.'+extension)
                if sibling.exists() or sibling.is_symlink():
                    checked('/'+sibling.relative_to(Path(target)).as_posix())
        session.revalidate()
        return audio, analysis
    finally:
        session.close()

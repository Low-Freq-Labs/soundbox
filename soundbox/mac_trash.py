"""Same-volume, exclusive Trash move for already confirmed, journaled files.

Some File Provider locations reject NSFileManager's Trash request. The fallback
is an ordinary atomic move into the user's real Trash, never an unlink, copy or
permission change. The existing disposal tag and original-path receipt survive.
"""
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import stat
import sys
from uuid import UUID

from . import mac_xattrs
from .state_io import sync_dir

TAG='com.lowfreqlabs.soundbox.removal'


def fallback_name(path,tag):
    if str(UUID(tag))!=tag:raise ValueError('Invalid confirmed disposal identity')
    path=Path(path);tail=' ('+tag+')'+path.suffix
    room=255-len(os.fsencode(tail))
    stem=os.fsencode(path.stem)[:room].decode('utf-8',errors='ignore')
    return stem+tail


def exclusive_move(source,destination):
    """macOS renameatx_np(RENAME_EXCL) cannot replace an existing occupant."""
    if sys.platform!='darwin':raise OSError('Recoverable desktop disposal requires macOS')
    source,destination=Path(source),Path(destination)
    libc=ctypes.CDLL(None,use_errno=True)
    call=libc.renameatx_np
    call.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    call.restype=ctypes.c_int
    # Darwin AT_FDCWD=-2, RENAME_EXCL=0x4, from sys/stdio.h.
    if call(-2,os.fsencode(source),-2,os.fsencode(destination),4):
        code=ctypes.get_errno();raise OSError(code,os.strerror(code),str(source))


def move_confirmed(path,trash_root=None):
    """Called only after RemovalStore has attached its exact interruption tag."""
    path=Path(path);trash=Path(trash_root) if trash_root is not None else Path.home()/'.Trash'
    source=path.lstat();directory=trash.lstat()
    if (path.resolve()!=path or trash.resolve()!=trash or path.is_relative_to(trash)
            or not stat.S_ISREG(source.st_mode) or source.st_nlink!=1 or source.st_uid!=os.getuid()
            or not stat.S_ISDIR(directory.st_mode) or directory.st_uid!=os.getuid()
            or source.st_dev!=directory.st_dev):raise OSError('A verified same-volume user Trash is required')
    tag=mac_xattrs.get(path,TAG)
    if tag is None:raise OSError('Only an explicitly confirmed disposal may use the Trash fallback')
    destination=trash/fallback_name(path,tag.decode('ascii'))
    try:exclusive_move(path,destination)
    except OSError as exc:
        if exc.errno==errno.EEXIST:raise OSError('The confirmed Trash destination is occupied; original retained') from exc
        raise
    moved=destination.lstat()
    if (moved.st_dev,moved.st_ino)!=(source.st_dev,source.st_ino):raise OSError('Trash move did not preserve the original file identity')
    # Trash is privacy-protected: opening the directory for fsync requests
    # enumeration permission even though the exact owned file is accessible.
    # Sync the source rename transaction and the known target inode instead.
    sync_dir(path.parent)
    fd=os.open(destination,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
        fcntl.fcntl(fd,51)  # Darwin F_FULLFSYNC; also flush the device write cache.
    finally:os.close(fd)
    return destination


def check_synthetic(report_dir):
    """Packaged developer probe: only an owned TEMP file, never library music.

    Run through the signed bundle to exercise its actual macOS privacy identity.
    No directory listing or permission changes, and restore the exact probe.
    """
    import hashlib
    import json
    import tempfile
    from uuid import uuid4
    from .rekordbox_removal_store import RemovalStore
    report_dir=Path(report_dir);report_dir.mkdir(parents=True,exist_ok=True)
    area=Path(tempfile.mkdtemp(prefix='soundbox-trash-probe-')).resolve()
    tag=str(uuid4());source=area/('soundbox-owned-probe-'+tag+'.wav')
    payload=b'SoundBox private synthetic Trash permission probe\n'
    source.write_bytes(payload);mac_xattrs.set(source,TAG,tag.encode())
    trash=Path.home()/'.Trash';target=trash/fallback_name(source,tag)
    report=dict(source=str(source),target=str(target),success=False,restored=False)
    try:
        before=source.stat()
        moved=move_confirmed(source)
        owner=object.__new__(RemovalStore);owner.trash_root=trash
        record=dict(path=str(source),tag=tag,sha256=hashlib.sha256(payload).hexdigest())
        if owner._trash_location(record)!=moved:raise OSError('Exact probe recovery failed')
        if moved.stat().st_ino!=before.st_ino or moved.read_bytes()!=payload:
            raise OSError('Probe identity or bytes changed')
        report['success']=True
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        try:
            if not source.exists() and target.is_file() and mac_xattrs.get(target,TAG)==tag.encode():
                exclusive_move(target,source)
            report['restored']=source.is_file() and source.read_bytes()==payload
            if report['restored']:
                source.unlink();area.rmdir()
        finally:(report_dir/'trash-probe.json').write_text(json.dumps(report,indent=2))

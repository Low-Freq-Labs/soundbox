"""Explicit whole-USB formatting. Never used by discovery or normal export."""
from dataclasses import dataclass
from pathlib import Path
import logging
import plistlib
import re
import subprocess
import time
from .sync import assert_target_safe

FORMATS = (('FAT32','FAT32 · widest player compatibility'),
           ('ExFAT','exFAT · newer players'))
_LOG = logging.getLogger('soundbox')


def _plist(*args):
    started=time.monotonic()
    result=subprocess.run(['/usr/sbin/diskutil',*args],capture_output=True,check=True,timeout=15)
    _LOG.info('USB format diskutil %s seconds=%.3f',' '.join(args),time.monotonic()-started)
    if len(result.stdout)>4*1024*1024:raise ValueError('Unexpected disk information')
    return plistlib.loads(result.stdout)


@dataclass(frozen=True)
class FormatTarget:
    target: str
    mount: tuple[int,int]
    volume_uuid: str
    disk: str
    disk_identity: tuple
    capacity: int
    volumes: tuple[str,...]
    reusable_partition: str = ''
    filesystem: str = ''


def capture(target, *, token, volume_uuid, sources=()):
    path=Path(target)
    if path.is_symlink() or path.parent!=Path('/Volumes') or not path.is_mount():
        raise ValueError('Select a mounted external USB')
    info=_plist('info','-plist',str(path))
    current=path.stat()
    if current.st_dev!=token or info.get('VolumeUUID')!=volume_uuid or info.get('MountPoint')!=str(path):
        raise ValueError('The selected USB changed')
    disk=info.get('ParentWholeDisk') or info.get('PartOfWhole')
    if not isinstance(disk,str) or not re.fullmatch(r'disk[1-9][0-9]*',disk):
        raise ValueError('A physical USB disk is required')
    whole=_plist('info','-plist',disk)
    if whole.get('Internal') is not False or whole.get('VirtualOrPhysical')!='Physical' or whole.get('BusProtocol')!='USB':
        raise ValueError('Will only format a physical external USB disk')
    listing=_plist('list','-plist',disk)
    disks=listing.get('AllDisksAndPartitions',[])
    if len(disks)!=1 or disks[0].get('DeviceIdentifier')!=disk:raise ValueError('Ambiguous USB disk')
    volumes=[]
    for partition in disks[0].get('Partitions',[]):
        mount=partition.get('MountPoint')
        if not mount:
            # EFI/partition-map entries contain no user volume. An unmounted
            # data partition cannot be checked against the source library.
            if partition.get('Content') not in ('EFI','Apple_partition_map','Apple_Free'):
                raise ValueError('Mount every data volume on this USB before reformatting')
            continue
        root=Path(mount)
        assert_target_safe(root,list(sources))
        if any((root/marker).exists() or (root/marker).is_symlink() for marker in ('iPod_Control','.rockbox')):
            raise ValueError('iPod and Rockbox disks cannot be reformatted here')
        volumes.append(str(root))
    if str(path) not in volumes:raise ValueError('USB volume is missing from its physical disk')
    identity=tuple(whole.get(k) for k in ('DeviceIdentifier','TotalSize','IORegistryEntryName','MediaName','DiskUUID'))
    capacity=int(whole.get('TotalSize') or 0)
    partitions=disks[0].get('Partitions',[])
    filesystem=info.get('FilesystemType','').lower()
    partition_type={'msdos':'DOS_FAT_32','exfat':'Windows_NTFS'}.get(filesystem)
    # Recreating an already-correct map makes diskutil tear down and rediscover
    # the whole disk. A same-filesystem quick erase needs only the existing
    # partition. Require the normal full-capacity, first-partition MBR layout;
    # every other layout still goes through whole-disk repartitioning.
    reusable=''
    if (disks[0].get('Content')=='FDisk_partition_scheme' and len(partitions)==1
            and partitions[0].get('DeviceIdentifier')==disk+'s1'
            and partitions[0].get('MountPoint')==str(path)
            and partition_type and partitions[0].get('Content')==partition_type
            and capacity>2*1024*1024
            and capacity-2*1024*1024<=int(partitions[0].get('Size') or 0)<=capacity):
        reusable=disk+'s1'
    return FormatTarget(str(path),(current.st_dev,current.st_ino),volume_uuid,disk,identity,
                        capacity,tuple(volumes),reusable,filesystem)


def name_for(name, filesystem):
    if filesystem not in dict(FORMATS):raise ValueError('Unsupported player filesystem')
    if not name or name!=name.strip() or any(c in '/\\:\0' or ord(c)<32 for c in name):
        raise ValueError('Enter a USB name without slashes or control characters')
    if filesystem=='FAT32':
        if len(name)>11 or not name.isascii() or any(c in '"*+,.;<=>?[]|' for c in name):
            raise ValueError('FAT32 names need 1–11 plain characters; spaces are allowed')
        return name.upper()
    if len(name)>27:raise ValueError('Use a USB name of 27 characters or fewer')
    return name


def format_usb(plan, name, filesystem, *, sources=()):
    started=time.monotonic()
    name=name_for(name,filesystem)
    fresh=capture(plan.target,token=plan.mount[0],volume_uuid=plan.volume_uuid,sources=sources)
    if fresh!=plan:raise ValueError('The USB changed since the format dialog opened')
    # This is the only destructive command. The UI has already presented this
    # exact disk, all its volumes, chosen format and final name for confirmation.
    expected_fs='msdos' if filesystem=='FAT32' else 'exfat'
    if plan.reusable_partition and plan.filesystem==expected_fs:
        command=['/usr/sbin/diskutil','eraseVolume',filesystem,name,plan.reusable_partition]
    else:
        command=['/usr/sbin/diskutil','eraseDisk',filesystem,name,'MBRFormat',plan.disk]
    erase_started=time.monotonic()
    result=subprocess.run(command,capture_output=True,text=True)
    _LOG.info('USB format %s disk=%s seconds=%.3f',command[1],plan.disk,time.monotonic()-erase_started)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-2000:])
    listing=_plist('list','-plist',plan.disk)
    disks=listing.get('AllDisksAndPartitions',[])
    if (len(disks)!=1 or disks[0].get('DeviceIdentifier')!=plan.disk
            or disks[0].get('Content')!='FDisk_partition_scheme'):
        raise RuntimeError('USB formatted; the partition map did not match the requested MBR layout')
    partitions=disks[0].get('Partitions',[])
    if len(partitions)!=1 or partitions[0].get('DeviceIdentifier')!=plan.disk+'s1':
        raise RuntimeError('USB formatted; expected one data partition before initialization')
    whole=_plist('info','-plist',plan.disk)
    identity=tuple(whole.get(k) for k in ('DeviceIdentifier','TotalSize','IORegistryEntryName','MediaName'))
    if (identity!=plan.disk_identity[:4] or whole.get('Internal') is not False
            or whole.get('VirtualOrPhysical')!='Physical' or whole.get('BusProtocol')!='USB'):
        raise RuntimeError('USB formatted; the physical device changed before initialization')
    volumes=[]
    for disk in disks:
        for partition in disk.get('Partitions',[]):
            if partition.get('VolumeName')==name and partition.get('MountPoint'):
                volumes.append(_plist('info','-plist',partition['DeviceIdentifier']))
    if len(volumes)!=1:raise RuntimeError('USB formatted; reconnect it to finish initializing settings')
    info=volumes[0]
    if (not info.get('VolumeUUID') or info.get('Internal') is not False
            or info.get('ParentWholeDisk',info.get('PartOfWhole'))!=plan.disk
            or info.get('DeviceIdentifier')!=partitions[0]['DeviceIdentifier']
            or info.get('MountPoint')!=partitions[0].get('MountPoint')
            or info.get('FilesystemType','').lower()!=expected_fs
            or info.get('VolumeName')!=name):
        raise RuntimeError('USB formatted; reconnect it to finish initializing settings')
    _LOG.info('USB format complete disk=%s seconds=%.3f',plan.disk,time.monotonic()-started)
    return info

"""External-device detection & helpers (macOS): diskutil probing, the
external-only safety guards, filesystem type (Rockbox needs FAT32), free space.

Everything here is read-only probing — no device writes.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from soundbox.device_profile import DeviceProfile, FAT_FILESYSTEMS, inspect_device
from soundbox.device_readiness import DeviceWriteReadiness, stock_write_readiness
from soundbox.paths import is_external_device_path
from soundbox.stock_ipod_identity import StockIdentityObservation, hardware_model, model_from_serial

MAX_DEVICES = 5


def human(n) -> str:
    # Decimal units (1 GB = 1000 MB) to match macOS Finder's free-space display.
    if n is None:
        return "?"
    f = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1000 or u == "TB":
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} TB"


# diskutil is slow; a volume's external-ness never changes, so cache per path.
# We ONLY cache a definitive answer from a SUCCESSFUL diskutil probe. A transient
# failure (diskutil timed out because the stick was busy, a momentary hiccup) must
# NOT be cached as False — doing that permanently dropped a still-mounted USB from
# the list until the app restarted (the "USB disappeared overnight" bug).
_EXTERNAL_CACHE: dict[str, bool] = {}
# Filesystem type per volume, captured from the same successful diskutil probe.
# Rockbox devices MUST be FAT32 ("msdos") — HFS+/exFAT iPods won't run Rockbox,
# so the sidebar warns the moment a non-FAT device mounts.
_FS_CACHE: dict[str, str] = {}
_PROFILE_CACHE: dict[str, DeviceProfile] = {}
_INFO_CACHE: dict[str, dict] = {}
_IPOD_FACTS_CACHE: dict[str, dict[str, str]] = {}
_USB_IDENTITY_CACHE: dict[str, tuple[tuple, StockIdentityObservation]] = {}
_USB_IDENTITY_RETRY: dict[str, tuple[tuple, float, int, object]] = {}
_USB_IDENTITY_LOCK = threading.Lock()
_IPOD_FACTS_OWNERS: dict[str, object] = {}


@dataclass(frozen=True)
class DeviceFacts:
    volume_uuid: str = ""
    protocol: str = ""
    device_identifier: str = ""
    filesystem_name: str = ""
    media_type: str = ""
    capacity_bytes: int = 0
    free_bytes: int = 0
    writable: bool = False
    usb_serial: str = ""
    hardware_serial: str = ""
    serial_source: str = ""
    firmware_version: str = ""
    firmware_source: str = ""
    transport_revision: str = ""
    firewire_guid: str = ""
    usb_family: str = ""
    model_number: str = ""
    model_source: str = ""
    identity_conflict: bool = False


def _volume_writable(info: dict) -> bool:
    """Honor explicit media/volume refusals; unknown facts grant no write access."""
    positive = ('WritableVolume', 'WritableMedia', 'Writable')
    negative = ('ReadOnlyVolume', 'ReadOnlyMedia')
    for key in (*positive, *negative):
        if key in info and type(info[key]) is not bool:
            return False
    if any(info.get(key) is False for key in positive if key in info):
        return False
    if any(info.get(key) is True for key in negative if key in info):
        return False
    return (info.get('WritableVolume') is True or info.get('Writable') is True
            or (info.get('ReadOnlyVolume') is False and info.get('ReadOnlyMedia') is False))


def device_target_profile(profile, role: str = ''):
    """Project an explicit export role without changing physical protection."""
    if role in ('', None):
        return profile
    if (role != 'ipod_usb' or not isinstance(profile, DeviceProfile)
            or not profile.has_stock_ipod
            or profile.filesystem not in FAT_FILESYSTEMS | {'exfat'}):
        return None
    return replace(profile, kind='ipod_usb', label='USB', stock_preview=False,
                   stock_write=False, has_stock_ipod=True, rockbox_export=False)


def device_export_readiness(profile, facts: DeviceFacts, mount_token: int, *,
                            stock_snapshot=None) -> DeviceWriteReadiness:
    """Explain cached target readiness without probing or inferring hardware."""
    if profile is None:
        return DeviceWriteReadiness(False, 'Device details are still being read')
    if facts.writable is not True:
        return DeviceWriteReadiness(False, 'The connected volume is read-only')
    if not facts.volume_uuid or not mount_token:
        return DeviceWriteReadiness(False, 'The connected device identity is unavailable')
    kind = getattr(profile, 'kind', '')
    if kind in {'stock_ipod', 'dual_boot'}:
        return stock_write_readiness(writable=facts.writable,
            filesystem=getattr(profile, 'filesystem', '') or getattr(stock_snapshot, 'filesystem', ''),
            stock_format=getattr(stock_snapshot, 'stock_format', None) or getattr(profile, 'stock_format', None),
            firewire_guid=facts.firewire_guid)
    if kind in {'generic_usb', 'ipod_usb'} and getattr(profile, 'cdj_export', False):
        return DeviceWriteReadiness(True, '')
    if kind == 'rockbox' and getattr(profile, 'rockbox_export', False):
        return DeviceWriteReadiness(True, '')
    reason = ('Rockbox export requires FAT32' if kind == 'rockbox'
              else 'USB export requires FAT32 or exFAT' if kind in {'generic_usb', 'ipod_usb'}
              else 'This device layout does not support export')
    return DeviceWriteReadiness(False, reason)


def _ioreg_records(*args: str) -> list[dict]:
    """Return a bounded IORegistry plist query, or no records on any failure."""
    try:
        result = subprocess.run(
            ["ioreg", *args, "-a"], capture_output=True, timeout=3)
        if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
            return []
        value = plistlib.loads(result.stdout)
    except Exception:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _classic_ipod_preferences(
        usb_serial: str, preferences_path: Path | None = None, *,
        hardware_serial: str = '') -> dict[str, str]:
    """Read Finder's classic-iPod cache without confusing it with USB transport.

    Apple stores the user-facing firmware and etched hardware serial here.  We
    accept an exact device-ID match, or a unique exact hardware serial observed
    on this mounted iPod. Disk-mode USB serials may themselves be that etched
    serial. An unrelated sole historical record never supplies an identity.
    """
    path = preferences_path or (
        Path.home() / "Library" / "Preferences" / "com.apple.iPod.plist")
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(handle)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 4 * 1024 * 1024:return {}
            with os.fdopen(handle, 'rb', closefd=False) as stream:raw = stream.read(4 * 1024 * 1024 + 1)
            after = os.fstat(handle);named = path.lstat()
            epoch = lambda row:(row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
            if len(raw) > 4 * 1024 * 1024 or not epoch(before) == epoch(after) == epoch(named):return {}
        finally:os.close(handle)
        value = plistlib.loads(raw)
    except Exception:
        return {}
    devices = value.get("Devices") if isinstance(value, dict) else None
    if not isinstance(devices, dict):
        return {}
    candidates = [
        (str(key), item) for key, item in devices.items()
        if isinstance(item, dict)
        and str(item.get("Device Class") or "").casefold() == "ipod"
    ]
    def device_id(value):
        return value.upper() if re.fullmatch(r'[0-9A-Fa-f]{16}',value) else value
    exact = [item for key, item in candidates if usb_serial and device_id(key) == device_id(usb_serial)]
    if len(exact) > 1:return {}
    if len(exact) == 1:
        record = exact[0]
        if hardware_serial and record.get('Serial Number') != hardware_serial:return {}
    else:
        serials = {value for value in (hardware_serial,
            usb_serial if not re.fullmatch(r'[0-9A-Fa-f]{16}',usb_serial) else '') if value}
        if len(serials) != 1:return {}
        matches = [item for _key,item in candidates if isinstance(item.get('Serial Number'),str)
                   and item['Serial Number'] in serials]
        if len(matches) != 1:return {}
        record = matches[0]
    def text(key):
        value = record.get(key)
        return value if isinstance(value,str) and len(value) <= 128 and all(ord(c) >= 32 for c in value) else ''
    return {
        "hardware_serial": text("Serial Number"),
        "firmware_version": text("Firmware Version String"),
    }


def _ipod_hardware_for_bsd(records: list[dict], device_identifier: str) -> dict[str, str]:
    """Bind one mounted BSD node to its own USB ancestor, never a global device."""
    if not re.fullmatch(r'disk\d+(?:s\d+)*', device_identifier):
        return {}
    stack = [(item, None, '') for item in records]
    candidates = {}; visited = 0
    while stack:
        item, usb, revision = stack.pop(); visited += 1
        if visited > 16_384:
            return {}
        if not isinstance(item, dict):
            continue
        if item.get('IOObjectClass') in {'IOUSBHostDevice', 'IOUSBDevice'}:
            # A nested USB device starts a new ancestry. Other vendors cannot
            # inherit an enclosing Apple device's signing identity.
            usb = item if (type(item.get('idVendor')) is int and item['idVendor'] == 1452
                and str(item.get('USB Product Name') or item.get('kUSBProductString')
                        or item.get('IORegistryEntryName') or '').strip() == 'iPod') else None
            revision = ''
        if (str(item.get('Vendor Identification') or '').strip() == 'Apple'
                and str(item.get('Product Identification') or '').strip() == 'iPod'):
            revision = str(item.get('Product Revision Level') or '').strip() or revision
        if item.get('BSD Name') == device_identifier and usb is not None:
            serial = str(usb.get('USB Serial Number') or usb.get('kUSBSerialNumberString') or '')
            product = usb.get('idProduct')
            if type(product) is not int:
                continue
            entry_id = usb.get('IORegistryEntryID')
            key = ('entry', entry_id) if type(entry_id) is int else ('object', id(usb))
            candidates[key] = (serial, product, revision, entry_id if type(entry_id) is int else 0)
        children = item.get('IORegistryEntryChildren', ())
        if isinstance(children, (list, tuple)):
            stack.extend((child, usb, revision) for child in children)
    if len(candidates) != 1:
        return {}
    serial, product, revision, registry = next(iter(candidates.values()))
    from clickwheel.ipod.ipod_models import USB_PID_TO_MODEL  # type: ignore[import-untyped]
    family = USB_PID_TO_MODEL.get(product, ('', ''))[0]
    guid = serial.upper() if re.fullmatch(r'[0-9A-Fa-f]{16}', serial) and int(serial, 16) else ''
    return dict(usb_serial=serial, firewire_guid=guid, usb_family=family, transport_revision=revision,
                usb_registry_id=str(registry),usb_product_id=str(product))


def _stock_identity_for_volume(path: str):
    from soundbox.stock_ipod_identity import read_stock_identity
    try:return read_stock_identity(path)
    except (OSError,ValueError,RuntimeError):return None


def _usb_identity_for_volume(path: str, **context):
    from soundbox.ipod_usb_identity import read_usb_identity
    return read_usb_identity(path,**context)


def _root_identity(path: str) -> tuple[str, str]:
    try:
        row=Path(path).lstat()
        if stat.S_ISDIR(row.st_mode):return str(row.st_dev),str(row.st_ino)
    except OSError:pass
    return '', ''


def _complete_ipod_identity(observed) -> bool:
    return bool(observed is not None and not observed.identity_conflict
                and observed.serial_number and observed.firmware_version
                and (hardware_model(observed.model_number) or model_from_serial(observed.serial_number)[0]))


def _usb_identity_observation(path,info,hardware,root_identity):
    """One bounded read per complete mounted identity; failed reads back off."""
    serial=hardware.get('usb_serial','');bsd=str(info.get('DeviceIdentifier') or '')
    uuid=str(info.get('VolumeUUID') or '')
    registry=hardware.get('usb_registry_id','');product=hardware.get('usb_product_id','')
    if (not all((*root_identity,bsd,uuid,serial)) or not hardware.get('firewire_guid')
            or not registry.isdecimal() or int(registry)<=0
            or not product.isdecimal() or int(product)<=0):return None
    key=(*root_identity,bsd,uuid,serial,registry,product)
    with _USB_IDENTITY_LOCK:
        cached=_USB_IDENTITY_CACHE.get(path)
        if cached is not None and cached[0]==key:return cached[1]
        previous=_USB_IDENTITY_RETRY.get(path)
        if previous is not None and previous[0]==key and time.monotonic()<previous[1]:return None
        failures=previous[2] if previous is not None and previous[0]==key else 0
        token=object()
        _USB_IDENTITY_RETRY[path]=(key,float('inf'),failures,token)
    try:
        observed=_usb_identity_for_volume(path,device_identifier=bsd,volume_uuid=uuid,
            usb_serial=serial,usb_registry_id=int(registry),usb_product_id=int(product))
    except (OSError,ValueError,RuntimeError):observed=None
    with _USB_IDENTITY_LOCK:
        if _USB_IDENTITY_RETRY.get(path)!=(key,float('inf'),failures,token):return None
        if _root_identity(path)!=root_identity:
            _USB_IDENTITY_RETRY.pop(path,None);return None
        current=_INFO_CACHE.get(path) or {}
        if (str(current.get('DeviceIdentifier') or ''),str(current.get('VolumeUUID') or ''))!=(bsd,uuid):
            _USB_IDENTITY_RETRY.pop(path,None);return None
        if _complete_ipod_identity(observed):
            _USB_IDENTITY_CACHE[path]=(key,observed);_USB_IDENTITY_RETRY.pop(path,None)
        else:
            _USB_IDENTITY_CACHE.pop(path,None)
            _USB_IDENTITY_RETRY[path]=(key,time.monotonic()+min(120.0,15.0*2**min(failures,3)),failures+1,token)
    return observed


def _classic_ipod_facts(path: str) -> dict[str, str]:
    """Read-only hardware facts scoped to this volume's actual BSD ancestry."""
    token=object()
    with _USB_IDENTITY_LOCK:_IPOD_FACTS_OWNERS[path]=token
    info_owner = _INFO_CACHE.get(path)
    info = info_owner if info_owner is not None else {}
    identifier = str(info.get('DeviceIdentifier') or '')
    volume_uuid = str(info.get('VolumeUUID') or '')
    root_identity=_root_identity(path)
    cached = _IPOD_FACTS_CACHE.get(path)
    same_mount = bool(cached is not None and cached.get('_device_identifier') == identifier
                      and cached.get('_volume_uuid') == volume_uuid
                      and (cached.get('_root_device',''),cached.get('_root_inode',''))==root_identity)
    # -l is needed for descendant IOMedia BSD names in the IOService tree.
    if same_mount and cached.get('firewire_guid'):
        hardware = {key:cached.get(key,'') for key in
                    ('usb_serial','firewire_guid','usb_family','transport_revision','usb_registry_id','usb_product_id')}
    else:
        hardware = (_ipod_hardware_for_bsd(_ioreg_records('-r', '-n', 'iPod', '-l'), identifier)
                    if re.fullmatch(r'disk\d+(?:s\d+)*',identifier) else {})
    usb_serial = hardware.get('usb_serial', '')
    observed = _stock_identity_for_volume(path) if re.fullmatch(r'disk\d+(?:s\d+)*',identifier) else None
    observed_serial = getattr(observed,'serial_number','')
    identity_conflict = bool(getattr(observed,'identity_conflict',False))
    preferences = (_classic_ipod_preferences(usb_serial,hardware_serial=observed_serial)
                   if not identity_conflict and (usb_serial or observed_serial) else {})
    fallback=None
    complete=bool((observed_serial or preferences.get('hardware_serial',''))
                  and (getattr(observed,'firmware_version','') or preferences.get('firmware_version',''))
                  and (hardware_model(getattr(observed,'model_number',''))
                       or model_from_serial(observed_serial or preferences.get('hardware_serial',''))[0]))
    if not identity_conflict and not complete:
        fallback=_usb_identity_observation(path,info,hardware,root_identity)
        if fallback is not None:
            identity_conflict=bool(fallback.identity_conflict or
                observed_serial and fallback.serial_number and observed_serial!=fallback.serial_number or
                getattr(observed,'model_number','') and fallback.model_number
                and observed.model_number!=fallback.model_number)
            if identity_conflict:preferences={}
    hardware_serial = observed_serial or getattr(fallback,'serial_number','') or preferences.get('hardware_serial','')
    firmware = (getattr(observed,'firmware_version','') or getattr(fallback,'firmware_version','')
                or preferences.get('firmware_version',''))
    facts = {
        "usb_serial": usb_serial,
        "hardware_serial": hardware_serial,
        "serial_source": (getattr(observed,'serial_source','') if observed_serial else
                          getattr(fallback,'serial_source','') if getattr(fallback,'serial_number','') else
                          "macOS iPod device record" if hardware_serial else ""),
        "firmware_version": firmware,
        "firmware_source": (getattr(observed,'firmware_source','') if getattr(observed,'firmware_version','') else
                            getattr(fallback,'firmware_source','') if getattr(fallback,'firmware_version','') else
                            "macOS iPod device record" if firmware else ""),
        "model_number": getattr(observed,'model_number','') or getattr(fallback,'model_number',''),
        "model_source": getattr(observed,'model_source','') or getattr(fallback,'model_source',''),
        "_identity_conflict": 'true' if identity_conflict else '',
        "transport_revision": hardware.get('transport_revision', ''),
        "firewire_guid": hardware.get('firewire_guid', ''),
        "usb_family": hardware.get('usb_family', ''),
        "usb_registry_id": hardware.get('usb_registry_id',''),
        "usb_product_id": hardware.get('usb_product_id',''),
        "_device_identifier": identifier,
        "_volume_uuid": volume_uuid,
        "_root_device": root_identity[0],
        "_root_inode": root_identity[1],
    }
    current_root=_root_identity(path)
    with _USB_IDENTITY_LOCK:
        if (_IPOD_FACTS_OWNERS.get(path) is not token or _INFO_CACHE.get(path) is not info_owner
                or current_root!=root_identity):return {}
        if identity_conflict or hardware or any(getattr(observed,key,'') for key in ('model_number','serial_number','firmware_version')):
            _IPOD_FACTS_CACHE[path] = facts
    return facts


def _is_external(path: Path) -> bool:
    """True only for external/ejectable volumes (USB/iPod/MP3) — never system.

    Caches only the result of a successful probe (external-ness is stable per mount).
    A failed/timed-out probe returns False WITHOUT caching, so the next poll retries
    and a busy-but-present device reappears on its own."""
    key = str(path)
    if key in _EXTERNAL_CACHE:
        return _EXTERNAL_CACHE[key]
    try:
        p = subprocess.run(["diskutil", "info", "-plist", key],
                           capture_output=True, timeout=5)
    except Exception:
        return False  # transient (timeout/busy) — DON'T cache; retry next poll
    if p.returncode != 0:
        return False  # probe failed (volume busy?) — DON'T cache; retry next poll
    try:
        info = plistlib.loads(p.stdout)
    except Exception:
        return False  # unparseable output — DON'T cache; retry next poll
    result = (info.get("Internal") is not True
              and bool(info.get("Internal") is False
                       or info.get("Ejectable") or info.get("RemovableMedia")))
    _EXTERNAL_CACHE[key] = result  # definitive answer from a good probe — safe to keep
    _FS_CACHE[key] = info.get("FilesystemType") or ""
    _INFO_CACHE[key] = dict(info)
    return result


def device_fs(path) -> str:
    """Filesystem type of a probed volume ('msdos'=FAT, 'exfat', 'apfs', 'hfs', '')."""
    return _FS_CACHE.get(str(path), "")


def device_profile(path) -> DeviceProfile:
    """Return the current read-only profile for a previously probed volume."""
    key = str(path)
    profile = _PROFILE_CACHE.get(key)
    if profile is None:
        info = _INFO_CACHE.get(key) or {}
        hardware = (_classic_ipod_facts(key) if info.get('DeviceIdentifier') and
                    (str(info.get('MediaType') or '').casefold() == 'ipod'
                     or (Path(key) / 'iPod_Control').is_dir()) else {})
        profile = inspect_device(key, device_fs(key), firewire_guid=hardware.get('firewire_guid', ''))
        if not (getattr(profile.stock_format, 'requires_fwid', False)
                and not hardware.get('firewire_guid')):
            _PROFILE_CACHE[key] = profile
    return profile


def device_facts(path, *, cached_only: bool = False) -> DeviceFacts:
    """Stable display/identity facts from the successful diskutil probe."""
    key = str(path)
    if key not in _INFO_CACHE and not cached_only:
        _is_external(Path(key))
    info = _INFO_CACHE.get(key) or {}
    profile = _PROFILE_CACHE.get(key)
    ipod = (
        _classic_ipod_facts(key)
        if not cached_only and info.get('DeviceIdentifier') and (
            str(info.get("MediaType") or "").casefold() == "ipod"
            or getattr(profile,'has_stock_ipod',False) or (Path(key)/'iPod_Control').is_dir())
        else _IPOD_FACTS_CACHE.get(key, {})
    )
    if (ipod.get('_device_identifier') != str(info.get('DeviceIdentifier') or '')
            or ipod.get('_volume_uuid') != str(info.get('VolumeUUID') or '')):
        ipod = {}
    return DeviceFacts(
        volume_uuid=str(info.get("VolumeUUID") or ""),
        protocol=str(info.get("BusProtocol") or ""),
        device_identifier=str(info.get("DeviceIdentifier") or ""),
        filesystem_name=str(
            info.get("FilesystemUserVisibleName")
            or info.get("FilesystemName") or ""),
        media_type=str(info.get("MediaType") or ""),
        capacity_bytes=int(info.get("VolumeSize") or info.get("TotalSize") or 0),
        free_bytes=int(info.get("FreeSpace") or 0),
        writable=_volume_writable(info),
        usb_serial=ipod.get("usb_serial", ""),
        hardware_serial=ipod.get("hardware_serial", ""),
        serial_source=ipod.get("serial_source", ""),
        firmware_version=ipod.get("firmware_version", ""),
        firmware_source=ipod.get("firmware_source", ""),
        transport_revision=ipod.get("transport_revision", ""),
        firewire_guid=ipod.get("firewire_guid", ""),
        usb_family=ipod.get("usb_family", ""),
        model_number=ipod.get("model_number", ""),
        model_source=ipod.get("model_source", ""),
        identity_conflict=bool(ipod.get('_identity_conflict')),
    )


def forget_device_mount(path) -> None:
    """Retire disconnected mount facts without opening any device path."""
    key = str(path)
    _EXTERNAL_CACHE.pop(key, None)
    _FS_CACHE.pop(key, None)
    _PROFILE_CACHE.pop(key, None)
    _INFO_CACHE.pop(key, None)
    _IPOD_FACTS_CACHE.pop(key, None)
    with _USB_IDENTITY_LOCK:
        _USB_IDENTITY_CACHE.pop(key,None);_USB_IDENTITY_RETRY.pop(key,None)
        _IPOD_FACTS_OWNERS.pop(key,None)


def refresh_device_mount(path) -> bool:
    """Forget path-keyed facts after a proven same-name remount and reprobe.

    A volume can disappear and return between two polls under the same
    ``/Volumes`` name. Its filesystem device token proves that this is a new
    mount; retaining the former profile/UUID/model cache would attach the old
    device's identity to it.
    """
    forget_device_mount(path)
    return _is_external(Path(path))


def _external_volume_of(p):
    """The external/ejectable volume that p lives on (or IS), else None. Resolves
    symlinks first, so a link out of the device can't sneak past. Rejects system
    volumes (e.g. /Volumes/Macintosh HD) and anything outside /Volumes. Allows the
    mount root itself (fine for creating a folder there)."""
    try:
        rp = Path(p).resolve()
    except OSError:
        return None
    parts = rp.parts  # e.g. ('/', 'Volumes', 'IPOD-TEST', 'Music', 'x')
    if len(parts) < 3 or parts[1] != "Volumes":
        return None
    vol = Path("/Volumes") / parts[2]
    return vol if _is_external(vol) else None


def _on_external_device(p) -> bool:
    """True ONLY if p is a real item STRICTLY inside an external device (never the
    mount root, never a system volume, never reachable via a symlink out). The guard
    for deletes — a path-prefix check alone would let /Volumes/Macintosh HD or a
    mount root through."""
    try:
        rp = Path(p).resolve()
    except OSError:
        return False
    vol = _external_volume_of(rp)
    return vol is not None and rp != vol  # strictly inside, not the mount root


def _in_external_product_tree(p) -> bool:
    """True for another product's reserved tree on an external volume."""
    try:
        rp = Path(p).resolve()
    except OSError:
        return False
    volume = _external_volume_of(rp)
    if volume is None:
        return False
    try:
        relative = rp.relative_to(volume.resolve())
    except (OSError, ValueError):
        return False
    return is_external_device_path(relative)


_DISCOVERY_RETRY = False


def discovery_needs_retry() -> bool:
    return _DISCOVERY_RETRY


def list_devices() -> list[tuple[str, str, int | None]]:
    global _DISCOVERY_RETRY
    _DISCOVERY_RETRY = True  # exceptions remain retryable
    retry = False
    out = []
    vol = Path("/Volumes")
    if vol.is_dir():
        parent_device = vol.stat().st_dev
        children = list(vol.iterdir())
        present = {str(d) for d in children}
        # Forget cache entries for volumes no longer mounted, so a re-plugged stick
        # (same name) is probed fresh instead of trusting a stale answer.
        for stale in [k for k in _EXTERNAL_CACHE if k not in present]:
            _EXTERNAL_CACHE.pop(stale, None)
            _FS_CACHE.pop(stale, None)
            _PROFILE_CACHE.pop(stale, None)
            _INFO_CACHE.pop(stale, None)
            _IPOD_FACTS_CACHE.pop(stale, None)
            with _USB_IDENTITY_LOCK:
                _USB_IDENTITY_CACHE.pop(stale,None);_USB_IDENTITY_RETRY.pop(stale,None)
                _IPOD_FACTS_OWNERS.pop(stale,None)
        for d in sorted(children):
            if d.name.startswith("."):
                continue
            try:
                identity = d.stat()
                if not stat.S_ISDIR(identity.st_mode):
                    continue
                if identity.st_dev == parent_device:
                    # Old mountpoint directories and the system-volume alias
                    # are not failed external probes. A newly created mount
                    # directory may still be attaching; keep a short retry
                    # window before treating it as an ordinary local folder.
                    retry |= time.time() - identity.st_ctime < 30
                    continue
            except OSError:
                retry = True
                continue
            if not _is_external(d):
                retry |= str(d) not in _EXTERNAL_CACHE
                continue
            key=str(d)
            # A restoring iPod can mount before Apple creates iPod_Control.
            # Recheck only that provisional profile through discovery backoff;
            # ordinary USBs keep their no-idle-profile-probe behavior.
            if str(_INFO_CACHE.get(key,{}).get('MediaType','')).casefold()=='ipod':
                profile=_PROFILE_CACHE.get(key)
                if profile is None or profile.kind=='generic_usb':
                    _PROFILE_CACHE.pop(key,None)
                    retry |= device_profile(key).kind=='generic_usb'
            try:
                st = os.statvfs(d)
                free = st.f_bavail * st.f_frsize
            except OSError:
                free = None
            out.append((d.name, str(d), free))
    else:
        retry = True
    _DISCOVERY_RETRY = retry
    return out[:MAX_DEVICES]

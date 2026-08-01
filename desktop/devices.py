"""External-device detection & helpers (macOS): diskutil probing, the
external-only safety guards, filesystem type (Rockbox needs FAT32), free space.

Everything here is read-only probing — no device writes.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from soundbox.device_profile import DeviceProfile, inspect_device

MAX_DEVICES = 3


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
        usb_serial: str, preferences_path: Path | None = None) -> dict[str, str]:
    """Read Finder's classic-iPod cache without confusing it with USB transport.

    Apple stores the user-facing firmware and etched hardware serial here.  We
    accept an exact device-ID match, or the sole classic-iPod record.  If several
    historical iPods make the record ambiguous, we return nothing rather than
    attach another device's identity.
    """
    path = preferences_path or (
        Path.home() / "Library" / "Preferences" / "com.apple.iPod.plist")
    try:
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return {}
        value = plistlib.loads(path.read_bytes())
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
    exact = [item for key, item in candidates if key == usb_serial]
    if len(exact) == 1:
        record = exact[0]
    elif len(candidates) == 1:
        record = candidates[0][1]
    else:
        return {}
    return {
        "hardware_serial": str(record.get("Serial Number") or ""),
        "firmware_version": str(record.get("Firmware Version String") or ""),
    }


def _classic_ipod_facts(path: str) -> dict[str, str]:
    """Read-only macOS hardware facts for an unambiguously connected iPod."""
    cached = _IPOD_FACTS_CACHE.get(path)
    if cached is not None:
        return cached
    usb_records = [
        item for item in _ioreg_records("-p", "IOUSB", "-r", "-n", "iPod")
        if int(item.get("idVendor") or 0) == 1452
    ]
    usb_serial = ""
    if len(usb_records) == 1:
        usb_serial = str(
            usb_records[0].get("USB Serial Number")
            or usb_records[0].get("kUSBSerialNumberString") or "")
    scsi_records = [
        item for item in _ioreg_records("-r", "-c", "IOSCSIPeripheralDeviceNub")
        if str(item.get("Vendor Identification") or "").strip() == "Apple"
        and str(item.get("Product Identification") or "").strip() == "iPod"
    ]
    transport_revision = ""
    if len(scsi_records) == 1:
        transport_revision = str(
            scsi_records[0].get("Product Revision Level") or "").strip()
    preferences = _classic_ipod_preferences(usb_serial)
    facts = {
        "usb_serial": usb_serial,
        "hardware_serial": preferences.get("hardware_serial", ""),
        "serial_source": "macOS iPod device record" if preferences.get("hardware_serial") else "",
        "firmware_version": preferences.get("firmware_version", ""),
        "firmware_source": "macOS iPod device record" if preferences.get("firmware_version") else "",
        "transport_revision": transport_revision,
    }
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
        profile = inspect_device(key, device_fs(key))
        _PROFILE_CACHE[key] = profile
    return profile


def device_facts(path) -> DeviceFacts:
    """Stable display/identity facts from the successful diskutil probe."""
    key = str(path)
    if key not in _INFO_CACHE:
        _is_external(Path(key))
    info = _INFO_CACHE.get(key) or {}
    ipod = (
        _classic_ipod_facts(key)
        if str(info.get("MediaType") or "").casefold() == "ipod"
        else {}
    )
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
        writable=bool(info.get("WritableVolume") or info.get("Writable")),
        usb_serial=ipod.get("usb_serial", ""),
        hardware_serial=ipod.get("hardware_serial", ""),
        serial_source=ipod.get("serial_source", ""),
        firmware_version=ipod.get("firmware_version", ""),
        firmware_source=ipod.get("firmware_source", ""),
        transport_revision=ipod.get("transport_revision", ""),
    )


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


def list_devices() -> list[tuple[str, str, int | None]]:
    out = []
    vol = Path("/Volumes")
    if vol.is_dir():
        present = {str(vol / n.name) for n in vol.iterdir()}
        # Forget cache entries for volumes no longer mounted, so a re-plugged stick
        # (same name) is probed fresh instead of trusting a stale answer.
        for stale in [k for k in _EXTERNAL_CACHE if k not in present]:
            _EXTERNAL_CACHE.pop(stale, None)
            _FS_CACHE.pop(stale, None)
            _PROFILE_CACHE.pop(stale, None)
            _INFO_CACHE.pop(stale, None)
            _IPOD_FACTS_CACHE.pop(stale, None)
        for d in sorted(vol.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not _is_external(d):
                continue
            try:
                st = os.statvfs(d)
                free = st.f_bavail * st.f_frsize
            except OSError:
                free = None
            out.append((d.name, str(d), free))
    return out[:MAX_DEVICES]

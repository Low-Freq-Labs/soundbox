"""Read-only removable-device classification and stock-iPod planning.

Detection never creates, edits, or removes anything.  In particular, stock
iPod database support is preview-only until SoundBox has a separately tested
writer for Apple's model-specific database and hashing rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import plistlib


FAT_FILESYSTEMS = frozenset({"msdos", "fat32", "fat"})
_STOCK_DATABASES = ("iTunesDB", "iTunesCDB", "Library.itdb")


@dataclass(frozen=True)
class DeviceProfile:
    kind: str
    label: str
    filesystem: str
    has_rockbox: bool
    has_stock_ipod: bool
    rockbox_export: bool
    stock_preview: bool
    stock_write: bool
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _read_model(root: Path) -> str:
    """Best-effort model label from small, existing iPod metadata files."""
    candidates = (
        root / "iPod_Control" / "Device" / "SysInfoExtended",
        root / "iPod_Control" / "Device" / "SysInfo",
    )
    for path in candidates:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > 1024 * 1024:
            continue
        try:
            value = plistlib.loads(raw)
            if isinstance(value, dict):
                for key in ("ProductType", "ModelNumStr", "ModelNumber"):
                    if value.get(key):
                        return str(value[key])
        except Exception:
            pass
        try:
            for line in raw.decode("utf-8", "replace").splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip() in {
                        "ModelNumStr", "ModelNumber", "ProductType"}:
                    return value.strip()
        except Exception:
            pass
    return ""


def inspect_device(root: str | Path, filesystem: str = "") -> DeviceProfile:
    """Classify one mounted volume using read-only, bounded probes."""
    base = Path(root)
    fs = str(filesystem or "").lower()
    has_rockbox = (base / ".rockbox").is_dir()
    ipod_control = base / "iPod_Control"
    itunes = ipod_control / "iTunes"
    has_stock = ipod_control.is_dir() and (
        itunes.is_dir()
        or any((itunes / name).is_file() for name in _STOCK_DATABASES)
    )
    if has_rockbox and has_stock:
        kind, label = "dual_boot", "Rockbox + stock iPod"
    elif has_rockbox:
        kind, label = "rockbox", "Rockbox"
    elif has_stock:
        kind, label = "stock_ipod", "Stock iPod"
    else:
        kind, label = "generic_usb", "Generic USB"
    fat = fs in FAT_FILESYSTEMS
    return DeviceProfile(
        kind=kind,
        label=label,
        filesystem=fs,
        has_rockbox=has_rockbox,
        has_stock_ipod=has_stock,
        rockbox_export=fat and kind != "stock_ipod",
        stock_preview=has_stock,
        stock_write=False,
        model=_read_model(base) if has_stock else "",
    )


def plan_stock_ipod_export(
        profile: DeviceProfile, audio_paths=()) -> dict:
    """Return an honest no-write compatibility preview for a stock iPod."""
    suffixes: dict[str, int] = {}
    missing = 0
    for value in audio_paths:
        path = Path(value)
        if not path.is_file():
            missing += 1
            continue
        suffix = path.suffix.lower() or "(none)"
        suffixes[suffix] = suffixes.get(suffix, 0) + 1
    blockers = []
    if not profile.has_stock_ipod:
        blockers.append("No stock iPod_Control database was detected.")
    blockers.append(
        "SoundBox does not write iTunesDB yet; this preview makes no changes.")
    return {
        "device_kind": profile.kind,
        "model": profile.model,
        "track_count": sum(suffixes.values()),
        "missing_count": missing,
        "formats": dict(sorted(suffixes.items())),
        "writable": False,
        "blockers": blockers,
    }

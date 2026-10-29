"""Read-only removable-device classification and stock-iPod planning.

Detection never creates, edits, or removes anything.  Stock write capability
means only that the mounted model/database crosses the narrow, offline-tested
engine allowlist; an actual sync still requires a complete dry-run and explicit
commit through :mod:`soundbox.stock_ipod`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import plistlib
import struct


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
    library_persistent_id: int = 0

    @property
    def cdj_export(self) -> bool:
        return self.kind=='generic_usb' and self.filesystem in FAT_FILESYSTEMS | {'exfat'}

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


def _stock_database_compatible(root: Path) -> bool:
    """Bounded MA446 database probe: classic iTunesDB, checksum scheme zero."""
    itunes = root / "iPod_Control" / "iTunes"
    cdb = itunes / "iTunesCDB"
    try:
        if cdb.is_file() and cdb.stat().st_size:
            return False
    except OSError:
        return False
    database = itunes / "iTunesDB"
    try:
        with database.open("rb") as handle:
            header = handle.read(0x32)
    except OSError:
        return False
    return (
        len(header) == 0x32
        and header[:4] == b"mhbd"
        and struct.unpack_from("<H", header, 0x30)[0] == 0
    )


def _stock_library_persistent_id(root: Path) -> int:
    """Bounded stable identity field; unlike DatabaseID, writers preserve it."""
    database = root / "iPod_Control" / "iTunes" / "iTunesDB"
    try:
        with database.open("rb") as handle:
            header = handle.read(0x50)
    except OSError:
        return 0
    if len(header) < 0x50 or header[:4] != b"mhbd":
        return 0
    return int(struct.unpack_from("<Q", header, 0x48)[0])


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
        kind, label = "dual_boot", "Dual Boot iPod"
    elif has_rockbox:
        kind, label = "rockbox", "Rockbox"
    elif has_stock:
        kind, label = "stock_ipod", "Stock iPod"
    else:
        kind, label = "generic_usb", "USB"
    fat = fs in FAT_FILESYSTEMS
    model = _read_model(base) if has_stock else ""
    stock_write = False
    # Routing is derived from the mounted on-disk layout, not from a guessed
    # last-booted firmware. Dual boot uses the separately guarded composite
    # planner; this flag only describes whether the stock half is compatible.
    if has_stock and fat and _stock_database_compatible(base):
        from .stock_ipod import supports_stock_write
        stock_write = supports_stock_write(model)
    return DeviceProfile(
        kind=kind,
        label=label,
        filesystem=fs,
        has_rockbox=has_rockbox,
        has_stock_ipod=has_stock,
        rockbox_export=fat and kind != "stock_ipod",
        stock_preview=has_stock,
        stock_write=stock_write,
        model=model,
        library_persistent_id=(
            _stock_library_persistent_id(base) if has_stock else 0),
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
    if missing:
        blockers.append(f"{missing} source track(s) are missing.")
    unsupported = sum(
        count for suffix, count in suffixes.items()
        if suffix not in {".mp3", ".wav"})
    if unsupported:
        blockers.append(
            f"{unsupported} track(s) are outside the initial MP3/WAV boundary.")
    if not profile.stock_write:
        blockers.append(
            "This model/database is outside the validated MA446 write allowlist.")
    return {
        "device_kind": profile.kind,
        "model": profile.model,
        "track_count": sum(suffixes.values()),
        "missing_count": missing,
        "formats": dict(sorted(suffixes.items())),
        "writable": not blockers,
        "blockers": blockers,
    }

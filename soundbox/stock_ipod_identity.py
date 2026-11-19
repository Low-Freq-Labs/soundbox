"""Read-only Video/Classic identity observations and display-only catalog.

Catalog facts do not admit a writer, identify modified storage, or probe a DAC.
Sources: Apple's model identification page, gtkpod/libgpod's original
``src/itdb_device.c`` tables, and Rockbox's ipodvideo/ipod6g target configs.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import re
import stat
from xml.parsers.expat import ExpatError

from .device_staging import open_parent


@dataclass(frozen=True)
class HardwareModel:
    family: str
    generation: str
    dac_name: str


_VIDEO_2005 = HardwareModel('iPod Video', '5th generation (2005)', 'Wolfson WM8758')
_VIDEO_2006 = HardwareModel('iPod Video', '5.5th generation', 'Wolfson WM8758')
_CLASSIC_2007 = HardwareModel('iPod Classic', '6th generation (2007)', 'Cirrus Logic CS42L55')
_CLASSIC_2008 = HardwareModel('iPod Classic', '2008 revision (120 GB)', 'Cirrus Logic CS42L55')
_CLASSIC_2009 = HardwareModel('iPod Classic', 'Late 2009 (160 GB)', 'Cirrus Logic CS42L55')

# https://github.com/gtkpod/libgpod/blob/master/src/itdb_device.c
# The leading M is Apple's retail model prefix; libgpod stores its remainder.
_HARDWARE_CATALOG = {
    **dict.fromkeys(('MA002', 'MA146', 'MA003', 'MA147', 'MA452'), _VIDEO_2005),
    **dict.fromkeys(('MA444', 'MA446', 'MA448', 'MA450', 'MA664'), _VIDEO_2006),
    # Retain the already independently verified MA446 package designation.
    'MA446': HardwareModel('iPod Video', '5.5th generation', 'Wolfson WM8758BG'),
    **dict.fromkeys(('MB029', 'MB147', 'MB145', 'MB150'), _CLASSIC_2007),
    **dict.fromkeys(('MB562', 'MB565'), _CLASSIC_2008),
    **dict.fromkeys(('MC293', 'MC297'), _CLASSIC_2009),
}

# Unique Video/Classic suffixes from the same original table. WEE belongs to
# both MA146 and MA446 and must never resolve to either. Storage capacity and
# case engraving are not suffix evidence, especially on refurbished players.
_SERIAL_SUFFIX_MODELS = {
    **dict.fromkeys(('SZ9', 'WEC', 'WED', 'WEG', 'WEH', 'WEL'), 'MA002'),
    **dict.fromkeys(('TXK', 'TXM', 'WEF', 'WEJ', 'WEK'), 'MA146'),
    **dict.fromkeys(('SZA', 'SZU'), 'MA003'),
    **dict.fromkeys(('TXL', 'TXN'), 'MA147'),
    **dict.fromkeys(('V9K', 'V9L', 'WU9'), 'MA444'),
    **dict.fromkeys(('VQM', 'V9M', 'V9N'), 'MA446'),
    **dict.fromkeys(('V9P', 'V9Q'), 'MA448'),
    **dict.fromkeys(('V9R', 'V9S', 'V95', 'V96', 'WUC'), 'MA450'),
    'W9G': 'MA664',
    'Y5N': 'MB029', 'YMV': 'MB147', 'YMU': 'MB145', 'YMX': 'MB150',
    '2C5': 'MB562', '2C7': 'MB565', '9ZS': 'MC293', '9ZU': 'MC297',
}


def normalize_display_model(value: str) -> str:
    """Normalize an exact retail model label without changing writer admission."""
    text = str(value or '').strip().upper()
    match = re.fullmatch(r'M?([A-Z][0-9]{3})(?:[A-Z]{1,3}(?:/[A-Z])?)?', text)
    return 'M' + match.group(1) if match else ''


def hardware_model(value: str) -> HardwareModel | None:
    return _HARDWARE_CATALOG.get(normalize_display_model(value))


def model_from_serial(serial_number: str) -> tuple[str, str]:
    """Resolve only unambiguous hardware serial suffixes; never a USB GUID."""
    serial = ''.join(re.findall(r'[A-Z0-9]', str(serial_number or '').upper()))
    suffix = serial[-3:] if len(serial) >= 3 else ''
    if re.fullmatch(r'[0-9A-F]{16}', serial):
        return '', suffix
    return _SERIAL_SUFFIX_MODELS.get(suffix, ''), suffix


@dataclass(frozen=True)
class StockIdentityObservation:
    model_number: str = ''
    model_source: str = ''
    serial_number: str = ''
    serial_source: str = ''
    firmware_version: str = ''
    firmware_source: str = ''
    identity_conflict: bool = False


_MAX_SYSINFO = 1024 * 1024


def _string(value: object) -> str:
    if not isinstance(value, str):
        return ''
    text = value.strip()
    return text if len(text) <= 128 and all(ord(char) >= 32 for char in text) else ''


def parse_stock_identity(raw: bytes, source: str) -> StockIdentityObservation:
    """Interpret existing bounded plist or colon-separated SysInfo bytes."""
    if not raw or len(raw) > _MAX_SYSINFO:
        return StockIdentityObservation()
    try:
        parsed = plistlib.loads(raw)
    except (ValueError, TypeError, OverflowError, RecursionError, ExpatError):
        parsed = None
    if isinstance(parsed, dict):
        fields = parsed
    elif raw.lstrip().startswith((b'<?xml', b'<plist', b'bplist')):
        return StockIdentityObservation()
    else:
        fields = {}
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return StockIdentityObservation()
        for line in text.splitlines():
            key, separator, value = line.partition(':')
            if separator:
                key, value = key.strip(), value.strip()
                if key in fields and fields[key] != value:
                    return StockIdentityObservation(identity_conflict=True)
                fields[key] = value
    # ProductType (for example iPod_Classic) identifies a family, not a retail
    # model. It must not hide a later ModelNumStr in the same metadata file.
    model = next((label for key in ('ModelNumStr', 'ModelNumber')
                  if (label := normalize_display_model(_string(fields.get(key))))), '')
    serial = next((_string(fields.get(key)) for key in ('SerialNumber', 'pszSerialNumber')
                   if _string(fields.get(key))), '')
    firmware = next((_string(fields.get(key))
                     for key in ('FirmwareVersion', 'Firmware Version String', 'VisibleBuildID', 'visibleBuildID')
                     if re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', _string(fields.get(key)))), '')
    serial_model, _suffix = model_from_serial(serial)
    conflict = bool(model and serial_model and model != serial_model)
    return StockIdentityObservation(model, source if model else '',
        serial, source if serial else '', firmware, source if firmware else '', conflict)


def _epoch(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_metadata(root: Path, identity: tuple[int, int], filename: str) -> bytes:
    relative = Path('iPod_Control/Device') / filename
    with open_parent(root, identity, relative) as (parent, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(handle, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_dev != identity[0]
                    or before.st_size > _MAX_SYSINFO):
                return b''
            raw = stream.read(_MAX_SYSINFO + 1)
            after = os.fstat(stream.fileno())
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            current_root = root.stat()
            if (_epoch(before) != _epoch(after) or _epoch(before) != _epoch(named)
                    or (current_root.st_dev, current_root.st_ino) != identity):
                return b''
            with open_parent(root, identity, relative) as (current, _name):
                original_parent, current_parent = os.fstat(parent), os.fstat(current)
                if (original_parent.st_dev, original_parent.st_ino) != (current_parent.st_dev, current_parent.st_ino):
                    return b''
            return raw


def read_stock_identity(root: str | Path) -> StockIdentityObservation:
    """Read two existing metadata files only; no song scan or new device files."""
    try:
        target = Path(root).expanduser().resolve(strict=True)
        info = target.stat()
    except (OSError, RuntimeError):
        return StockIdentityObservation()
    identity = (info.st_dev, info.st_ino)
    values: dict[str, str] = {}
    conflict = False
    for name in ('SysInfoExtended', 'SysInfo'):
        try:
            observation = parse_stock_identity(_read_metadata(target, identity, name), name)
        except (OSError, RuntimeError):
            continue
        conflict = conflict or observation.identity_conflict
        for field in ('model_number', 'serial_number'):
            value = getattr(observation, field)
            if value and field in values and value.casefold() != values[field].casefold():
                conflict = True
        for field in ('model_number', 'model_source', 'serial_number', 'serial_source',
                      'firmware_version', 'firmware_source'):
            value = getattr(observation, field)
            if value and field not in values:
                values[field] = value
    model, _suffix = model_from_serial(values.get('serial_number', ''))
    if model and values.get('model_number') and model != values['model_number']:
        conflict = True
    return StockIdentityObservation(**values, identity_conflict=conflict)

"""Bounded read-only USB identity capture through the isolated native helper.

Only the caller's exact mounted BSD/USB object may be queried. Returned data
supplies display observations, never signing authority or device-file writes.
"""
from __future__ import annotations

import logging
from pathlib import Path
import plistlib
import re
import subprocess
import sys

from .stock_ipod_identity import StockIdentityObservation, parse_stock_identity

LOG = logging.getLogger('soundbox')
_MAX_REPLY = 1024 * 1024


def _helper_path() -> Path | None:
    if sys.platform != 'darwin':
        return None
    if getattr(sys, 'frozen', False):
        location = getattr(sys, '_MEIPASS', None)
        if not isinstance(location, (str, Path)) or not Path(location).is_absolute():
            return None
        base = Path(location)
    else:
        base = Path(__file__).resolve().parents[1] / 'build'
    helper = base / 'native/soundbox-ipod-identity'
    return helper if helper.is_file() else None


def _guid(value: object) -> str:
    if not isinstance(value, str):
        return ''
    text = value.strip().upper().removeprefix('0X')
    return text if re.fullmatch(r'[0-9A-F]{16}', text) and int(text, 16) else ''


def read_usb_identity(
    root: str | Path, *, device_identifier: str, volume_uuid: str,
    usb_serial: str, usb_registry_id: int, usb_product_id: int,
) -> StockIdentityObservation | None:
    """Capture one exact connected iPod's display facts, or return no claim.

    Call only in the serialized device worker when existing observations are
    incomplete. The caller owns per-mount positive caching and bounded retry.
    """
    serial = _guid(usb_serial)
    if (not serial or not isinstance(device_identifier, str) or not isinstance(volume_uuid, str)
            or not re.fullmatch(r'disk[0-9]+(?:s[0-9]+)*', device_identifier)
            or not re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', volume_uuid)
            or type(usb_registry_id) is not int or not 0 < usb_registry_id < 2**64
            or type(usb_product_id) is not int or not 0 < usb_product_id <= 0xffff):
        return None
    helper = _helper_path()
    if helper is None:
        return None
    try:
        target = Path(root).expanduser().resolve(strict=True)
        before = target.stat()
        identity = (before.st_dev, before.st_ino)
        if not target.is_dir():
            return None
        result = subprocess.run([
            str(helper), str(target), device_identifier, str(identity[0]), str(identity[1]),
            str(usb_registry_id), str(usb_product_id), serial, volume_uuid.upper(),
        ], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
        after = target.stat()
        if (after.st_dev, after.st_ino) != identity:
            return None
        if (result.returncode or not 0 < len(result.stdout) <= _MAX_REPLY
                or not result.stdout.startswith(b'bplist00')):
            return None
        value = plistlib.loads(result.stdout)
        if not isinstance(value, dict) or _guid(value.get('FireWireGUID')) != serial:
            return None
        return parse_stock_identity(result.stdout, 'USB extended SysInfo')
    except (OSError, RuntimeError, ValueError, TypeError, RecursionError, OverflowError,
            subprocess.TimeoutExpired):
        LOG.debug('No complete USB iPod identity observation for %s', device_identifier)
        return None

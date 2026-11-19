"""Read-only explanations for device admission; planners retain write authority."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .stock_ipod_format import StockWriteFormat


@dataclass(frozen=True)
class DeviceWriteReadiness:
    ready: bool
    reason: str

    @property
    def selectable(self) -> bool:
        return self.ready


def stock_write_readiness(
        *, writable: bool, filesystem: str, stock_format: StockWriteFormat | None,
        artwork: bool = False, firewire_guid: str = '') -> DeviceWriteReadiness:
    """Explain cached format capabilities; model labels grant no write authority."""
    if writable is not True:
        return DeviceWriteReadiness(False, 'The connected volume is read-only')
    filesystem = str(filesystem or '').casefold().replace(' ', '')
    if filesystem not in {'msdos', 'fat', 'fat32', 'ms-dos(fat32)', 'ms-dosfat32'}:
        return DeviceWriteReadiness(False, 'iPod writes require FAT32')
    if stock_format is None:
        return DeviceWriteReadiness(False, 'The connected iPod database format has not been read')
    if getattr(stock_format, 'requires_fwid', False):
        if not re.fullmatch(r'[0-9A-Fa-f]{16}', firewire_guid) or not int(firewire_guid, 16):
            return DeviceWriteReadiness(False, 'The connected iPod signing identity is unavailable')
        observed = getattr(stock_format, 'firewire_guid', '')
        if observed and observed.upper() != firewire_guid.upper():
            return DeviceWriteReadiness(False, 'The connected iPod signing identity changed; refresh it first')
    if not stock_format.writable:
        return DeviceWriteReadiness(False,
            stock_format.reason or 'This iPod database format is not supported')
    if artwork and not stock_format.supports_artwork:
        return DeviceWriteReadiness(False,
            stock_format.artwork_reason or 'This iPod artwork format is not supported')
    return DeviceWriteReadiness(True, '')

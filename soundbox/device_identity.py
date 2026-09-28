"""Remember observed iPod display facts; never grant write/mount authority."""
from dataclasses import replace

from .stock_ipod_info import StockIpodSnapshot


def remember_identity(
    snapshot: StockIpodSnapshot, records: dict,
) -> StockIpodSnapshot:
    """Use only the exact volume/library key; conflicting serial starts afresh."""
    if not snapshot.identity_key or not snapshot.volume_uuid or not snapshot.library_persistent_id:
        return snapshot
    raw = records.get(snapshot.identity_key)
    previous = raw if isinstance(raw, dict) else {}
    if (snapshot.serial_number and previous.get("serial_number")
            and snapshot.serial_number != previous["serial_number"]):
        previous = {}
    record = dict(previous)
    updates = {}
    for field, provenance in (("serial_number", "serial_source"),
                              ("firmware_version", "firmware_source")):
        value = getattr(snapshot, field)
        source = getattr(snapshot, provenance)
        if value and source and not source.startswith("Remembered: "):
            record[field] = value
            record[provenance] = source
        elif not value:
            saved, origin = record.get(field), record.get(provenance)
            if isinstance(saved, str) and saved and isinstance(origin, str) and origin:
                updates[field] = saved
                updates[provenance] = f"Remembered: {origin}"
    if record:
        records[snapshot.identity_key] = record
    return replace(snapshot,
        serial_number=updates.get("serial_number", snapshot.serial_number),
        serial_source=updates.get("serial_source", snapshot.serial_source),
        firmware_version=updates.get("firmware_version", snapshot.firmware_version),
        firmware_source=updates.get("firmware_source", snapshot.firmware_source))

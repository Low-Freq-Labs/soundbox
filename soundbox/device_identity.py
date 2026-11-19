"""Remember observed iPod display facts; never grant write/mount authority."""
from dataclasses import replace

from .stock_ipod_info import StockIpodSnapshot
from .stock_ipod_identity import hardware_model, normalize_display_model


def remember_identity(
    snapshot: StockIpodSnapshot, records: dict,
) -> StockIpodSnapshot:
    """Use only the exact volume/library key; conflicting serial starts afresh."""
    if not snapshot.identity_key or not snapshot.volume_uuid or not snapshot.library_persistent_id:
        return snapshot
    if snapshot.identity_conflict:
        records.pop(snapshot.identity_key, None)
        return snapshot
    raw = records.get(snapshot.identity_key)
    previous = raw if isinstance(raw, dict) else {}
    invalidated = False
    if (snapshot.serial_number and previous.get("serial_number")
            and snapshot.serial_number != previous["serial_number"]):
        previous = {}
        invalidated = True
    current_usb = snapshot.usb_serial.strip().upper()
    previous_usb = previous.get('usb_serial')
    same_serial = bool(snapshot.serial_number and snapshot.serial_number == previous.get('serial_number')
                       and not snapshot.serial_source.casefold().startswith('remembered'))
    if (current_usb and isinstance(previous_usb, str) and previous_usb
            and current_usb != previous_usb.upper() and not same_serial):
        previous = {}
        invalidated = True
    model = normalize_display_model(snapshot.model)
    observed_model = (hardware_model(model) is not None and snapshot.model_source
                      and not snapshot.model_source.casefold().startswith(
                          ('remembered', 'previous soundbox')))
    previous_model = hardware_model(previous.get('model', ''))
    if (observed_model and previous_model is not None
            and normalize_display_model(previous.get('model', '')) != model):
        previous = {}
        invalidated = True
    if (previous_model is not None and snapshot.usb_family in {'iPod Video', 'iPod Classic'}
            and snapshot.usb_family != previous_model.family):
        previous = {}
        invalidated = True
    if (invalidated and snapshot.model_source.casefold().startswith(
            ('remembered', 'previous soundbox'))):
        snapshot = replace(snapshot, model='', model_source='', generation='Unknown generation',
                           dac_name='', dac_source='', family=snapshot.usb_family or 'Classic stock iPod')
        model = ''
    record = dict(previous)
    updates = {}
    if current_usb:
        record['usb_serial'] = current_usb
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
    if observed_model:
        record['model'] = model
        record['model_source'] = snapshot.model_source
    elif not snapshot.model:
        raw_model = previous.get('model')
        saved = raw_model if isinstance(raw_model, str) else ''
        origin = previous.get('model_source')
        description = hardware_model(saved)
        if description is not None and isinstance(origin, str) and origin:
            updates.update(model=normalize_display_model(saved), model_source=f'Remembered: {origin}',
                           family=description.family, generation=description.generation,
                           dac_name=description.dac_name,
                           dac_source=f'Remembered: verified {normalize_display_model(saved)} hardware catalog')
    if record:
        records[snapshot.identity_key] = record
    return replace(snapshot,
        model=updates.get('model', snapshot.model),
        model_source=updates.get('model_source', snapshot.model_source),
        family=updates.get('family', snapshot.family),
        generation=updates.get('generation', snapshot.generation),
        dac_name=updates.get('dac_name', snapshot.dac_name),
        dac_source=updates.get('dac_source', snapshot.dac_source),
        serial_number=updates.get("serial_number", snapshot.serial_number),
        serial_source=updates.get("serial_source", snapshot.serial_source),
        firmware_version=updates.get("firmware_version", snapshot.firmware_version),
        firmware_source=updates.get("firmware_source", snapshot.firmware_source))

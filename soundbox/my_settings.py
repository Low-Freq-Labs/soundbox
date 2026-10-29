"""Byte-preserving USB player/mixer profiles; no desktop-preference access.

Maps exported player/mixer preferences, never rekordbox desktop preferences.
Native exported-file comparisons cover tempo, mixer and newer cue choices.
Preserve unknown bytes rather than
rebuilding files from a dependency's defaults. See docs/cdj-usb-export.md.
"""

import binascii
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


_REGISTRY = json.loads(Path(__file__).with_name("my_settings_fields.json").read_text())


def catalogue() -> dict:
    """Return candidate field choices/provenance, not device-write authority."""
    return dict(deepcopy(_REGISTRY))


def _validate(filename: str, raw: bytes) -> dict:
    if not isinstance(raw, bytes):
        raise ValueError("Expected immutable captured bytes")
    schema = _REGISTRY["formats"].get(filename)
    if schema is None:
        raise ValueError("Unsupported My Settings filename")
    end = 104 + schema["body_size"]
    if len(raw) != end + 4:
        raise ValueError("Unsupported My Settings file length")
    if struct.unpack_from("<I", raw)[0] != 96:
        raise ValueError("Unsupported My Settings string framing")
    for offset, name in ((4, "brand"), (36, "software"), (68, "version")):
        expected = schema[name].encode("ascii").ljust(32, b"\0")
        if raw[offset:offset + 32] != expected:
            raise ValueError(f"Unsupported My Settings {name}")
    if struct.unpack_from("<I", raw, 100)[0] != schema["body_size"]:
        raise ValueError("Unsupported My Settings body framing")
    actual = binascii.crc_hqx(raw[schema["checksum_start"]:end], 0)
    if actual != struct.unpack_from("<H", raw, end)[0]:
        raise ValueError("My Settings checksum mismatch")
    return dict(schema)


def inspect(filename: str, raw: bytes) -> dict:
    """Read supported fields; expose unrecognized values without guessing."""
    schema = _validate(filename, raw)
    fields = {}
    for name, field in schema["fields"].items():
        value,token = _field(raw,field)
        fields[name] = {"value": token, "raw": value, "recognized": token is not None,
                        "choices": list(field["values"])}
    return {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest(),
            "format_version": schema["version"], "fields": fields,
            "native_edit_accepted": False}


def _field(raw,field):
    width=field.get('width',1)
    value=([raw[at] for at in field['offsets']] if 'offsets' in field else
           raw[field['offset']] if width==1 else list(raw[field['offset']:field['offset']+width]))
    token=next((key for key,value_bytes in field['values'].items() if value_bytes==value),None)
    if token is None:
        token=next((alias['value'] for alias in field.get('aliases',[]) if alias['bytes']==value),None)
    return value,token


def patch(filename: str, raw: bytes, changes: dict[str, str], *,
          expected_sha256: str) -> bytes:
    """Return candidate bytes fenced to an exact base; never publish them.

    Only the selected field bytes and checksum may change. Unknown bytes,
    unrelated unrecognized values, original headers and footer are retained.
    A targeted unrecognized value fails closed instead of implying support.
    """
    if not isinstance(raw, bytes):
        raise ValueError("Expected immutable captured bytes")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("My Settings base changed")
    schema = _validate(filename, raw)
    replacements: list[tuple[int,bytes]] = []
    for name, token in changes.items():
        field = schema["fields"].get(name)
        if field is None:
            raise ValueError(f"Unsupported My Settings field: {name}")
        if not isinstance(token, str) or token not in field["values"]:
            raise ValueError(f"Unsupported choice for My Settings field: {name}")
        _value,current = _field(raw,field)
        if current is None:
            raise ValueError(f"Unrecognized existing My Settings value: {name}")
        if current!=token:
            value=field['values'][token]
            if 'offsets' in field:
                replacements.extend((at,bytes([byte])) for at,byte in zip(field['offsets'],value))
            else:replacements.append((field['offset'],bytes([value]) if isinstance(value,int) else bytes(value)))
    if not replacements:
        return raw
    candidate = bytearray(raw)
    for offset, value in replacements:
        candidate[offset:offset+len(value)] = value
    end = 104 + schema["body_size"]
    crc = binascii.crc_hqx(candidate[schema["checksum_start"]:end], 0)
    struct.pack_into("<H", candidate, end, crc)
    result = bytes(candidate)
    _validate(filename, result)
    return result


def values(files) -> dict:
    """Recognized logical values only; unknown/missing device fields stay local."""
    result = {}
    for path, raw in files:
        name = Path(path).name
        if name not in _REGISTRY['formats']:
            continue
        try:
            view = inspect(name, raw)
        except ValueError:
            continue
        result[name] = {key: field['value'] for key, field in view['fields'].items()
                        if field['recognized']}
    return result


def validate(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError('My Settings must be a profile')
    result = {}
    for name, changes in profile.items():
        schema = _REGISTRY['formats'].get(name)
        if schema is None or not isinstance(changes, dict):
            raise ValueError('Unsupported My Settings profile')
        for key, token in changes.items():
            if key not in schema['fields'] or token not in schema['fields'][key]['values']:
                raise ValueError('Unsupported My Settings value')
        result[name] = dict(changes)
    return result


def new_files(profile: dict) -> dict[str, bytes]:
    """Fresh-device templates only. Existing USB files are always patched."""
    profile = validate(profile)
    result = {}
    for name, template in _REGISTRY['templates'].items():
        raw = bytes.fromhex(template)
        result[name] = patch(name, raw, profile.get(name, {}),
                             expected_sha256=hashlib.sha256(raw).hexdigest())
    return result

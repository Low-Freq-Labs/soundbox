"""Candidate bytes-only My Settings editor; no device/source write APIs.

Maps exported player/mixer preferences, never rekordbox desktop preferences.
One native tempo-output reference matches; device-write acceptance is pending.
Preserve unknown bytes rather than
rebuilding files from a dependency's defaults. See MY_SETTINGS_PLAN.md.
"""

import binascii
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


_REGISTRY = json.loads(Path(__file__).with_name("mysettings_fields.json").read_text())


def catalogue() -> dict:
    """Return candidate field choices/provenance, not device-write authority."""
    return deepcopy(_REGISTRY)


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
    return schema


def inspect(filename: str, raw: bytes) -> dict:
    """Read supported fields; expose unrecognized values without guessing."""
    schema = _validate(filename, raw)
    fields = {}
    for name, field in schema["fields"].items():
        value = raw[field["offset"]]
        token = next((k for k, v in field["values"].items() if v == value), None)
        fields[name] = {"value": token, "raw": value, "recognized": token is not None,
                        "choices": list(field["values"])}
    return {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest(),
            "format_version": schema["version"], "fields": fields,
            "native_edit_accepted": False}


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
    replacements = []
    for name, token in changes.items():
        field = schema["fields"].get(name)
        if field is None:
            raise ValueError(f"Unsupported My Settings field: {name}")
        if not isinstance(token, str) or token not in field["values"]:
            raise ValueError(f"Unsupported choice for My Settings field: {name}")
        offset = field["offset"]
        if raw[offset] not in field["values"].values():
            raise ValueError(f"Unrecognized existing My Settings value: {name}")
        replacements.append((offset, field["values"][token]))
    if all(raw[offset] == value for offset, value in replacements):
        return raw
    candidate = bytearray(raw)
    for offset, value in replacements:
        candidate[offset] = value
    end = 104 + schema["body_size"]
    crc = binascii.crc_hqx(candidate[schema["checksum_start"]:end], 0)
    struct.pack_into("<H", candidate, end, crc)
    result = bytes(candidate)
    _validate(filename, result)
    return result

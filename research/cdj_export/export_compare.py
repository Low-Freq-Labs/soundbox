"""Native-reference comparison for explicit offline export fixtures only.

Reports strict byte equality separately from decoded database equality. A raw
mismatch is always retained in the report; no field is silently normalized.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import zlib

from research.cdj_export.pdb_audit import audit


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_rows(path: Path, key_source: Path) -> dict:
    """Read a disposable SQLCipher copy, including captured WAL/SHM if present."""
    from sqlcipher3 import dbapi2 as sqlcipher
    source = key_source.read_text()
    blob = re.search(r'const BLOB: &str = "([^"]+)";', source).group(1)
    xor = re.search(r'const XOR_KEY: &\[u8\] = b"([^"]+)";', source).group(1).encode()
    decoded = base64.b85decode(blob)
    key = zlib.decompress(bytes(byte ^ xor[i % len(xor)] for i, byte in enumerate(decoded))).decode()
    with tempfile.TemporaryDirectory(prefix="soundbox-export-compare-") as directory:
        target = Path(directory).resolve() / "exportLibrary.db"
        for suffix in ("", "-wal", "-shm"):
            original = Path(str(path) + suffix)
            if original.exists():
                if original.is_symlink():
                    raise ValueError("SQLCipher companion is a symlink")
                shutil.copyfile(original, Path(str(target) + suffix))
        conn = sqlcipher.connect(target.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA key='" + key.replace("'", "''") + "'")
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA trusted_schema=OFF")
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise ValueError("SQLCipher integrity check failed")
            tables, columns = {}, {}
            declarations = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"))
            names = list(declarations)
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                cur = conn.execute("SELECT * FROM " + quoted)
                keys = [column[0] for column in cur.description]
                tables[name] = sorted((dict(zip(keys, row)) for row in cur.fetchall()),
                                      key=lambda row: json.dumps(row, sort_keys=True))
                columns[name] = [list(row) for row in conn.execute("PRAGMA table_info(" + quoted + ")")]
            indexes = sorted(conn.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index'").fetchall())
            views_and_triggers = sorted(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('view','trigger')").fetchall())
            protocol = {name: conn.execute("PRAGMA " + name).fetchone()[0]
                        for name in ("user_version", "application_id", "encoding")}
            return {"tables": tables, "columns": columns, "indexes": [list(row) for row in indexes],
                    "declarations": declarations, "views_and_triggers": [list(row) for row in views_and_triggers],
                    "protocol": protocol}
        finally:
            conn.close()


def device_string(row: bytes, offset: int) -> tuple[str, int]:
    if not 0 <= offset < len(row):
        raise ValueError("DeviceSQL string pointer outside row")
    kind = row[offset]
    if kind in (0x40, 0x90):
        if offset % 4:
            raise ValueError("Unaligned long DeviceSQL string")
        length = struct.unpack_from("<H", row, offset + 1)[0]
        if length < 4 or offset + length > len(row):
            raise ValueError("Invalid long DeviceSQL string")
        return row[offset + 4:offset + length].decode("ascii" if kind == 0x40 else "utf-16-le"), length
    length = kind >> 1
    if not kind & 1 or length < 1 or offset + length > len(row):
        raise ValueError("Invalid short DeviceSQL string")
    return row[offset + 1:offset + length].decode("ascii"), length


def pdb_rows(data: bytes) -> dict[int, list[bytes]]:
    report = audit(data)
    if report["issues"]:
        raise ValueError("PDB structure failed: " + repr(report["issues"]))
    size = report["page_size"]
    result = {}
    for table in report["tables"]:
        bodies = []
        for info in table["pages"]:
            if info["flags"] & 0x40:
                continue
            page = data[info["index"] * size:(info["index"] + 1) * size]
            offsets = []
            for slot in range(info["slots"]):
                group, within = divmod(slot, 16)
                end = size - group * 36
                if struct.unpack_from("<H", page, end - 4)[0] & (1 << within):
                    offsets.append(struct.unpack_from("<H", page, end - 6 - within * 2)[0])
            positions = sorted(offsets)
            bounds = dict(zip(positions, positions[1:] + [struct.unpack_from("<H", page, 30)[0]]))
            bodies.extend(page[40 + start:40 + bounds[start]] for start in offsets)
        result[table["type"]] = bodies
    return result


def classic_semantics(data: bytes) -> dict:
    result = {}
    scalar32 = ["content_link", "sample_rate", "composer_id", "file_size",
                "master_content_id", "master_db_id", "artwork_id", "key_id",
                "original_artist_id", "label_id", "remixer_id", "bitrate",
                "track_number", "bpm_x100", "genre_id", "album_id", "artist_id", "id"]
    for kind, rows in pdb_rows(data).items():
        decoded = []
        for row in rows:
            if kind == 0:
                item = {name: struct.unpack_from("<I", row, 4 + i * 4)[0]
                        for i, name in enumerate(scalar32)}
                for name, offset in (("subtype", 0), ("disc", 76), ("play_count", 78),
                                     ("year", 80), ("bit_depth", 82), ("duration", 84),
                                     ("trailer1", 86), ("format", 90), ("trailer2", 92)):
                    item[name] = struct.unpack_from("<H", row, offset)[0]
                item.update(color=row[88], rating=row[89])
                item["strings"] = [device_string(row, struct.unpack_from("<H", row, 94 + 2 * i)[0])[0]
                                   for i in range(21)]
            elif kind == 7:
                item = dict(zip(("parent", "reserved", "sequence", "id", "folder"), struct.unpack_from("<5I", row)))
                item["name"] = device_string(row, 20)[0]
            elif kind == 8:
                item = dict(zip(("position", "track_id", "playlist_id"), struct.unpack_from("<3I", row)))
            elif kind in (1, 4, 5, 13):
                item = {"id": struct.unpack_from("<I", row)[0], "name": device_string(row, 8 if kind == 5 else 4)[0]}
            elif kind == 2:
                offset = struct.unpack_from("<H", row, 10)[0] if row[0] == 0x64 else row[9]
                item = {"id": struct.unpack_from("<I", row, 4)[0], "name": device_string(row, offset)[0]}
            elif kind == 3:
                item = {"artist_id": struct.unpack_from("<I", row, 8)[0],
                        "id": struct.unpack_from("<I", row, 12)[0], "name": device_string(row, row[21])[0]}
            elif kind == 6:
                item = {"id": row[4], "name": device_string(row, 8)[0]}
            elif kind == 19:
                date, length = device_string(row, 12)
                at = 12 + length + 2
                version, length = device_string(row, at)
                name = device_string(row, at + length)[0]
                item = {"contents": struct.unpack_from("<I", row, 4)[0],
                        "background": row[9], "date": date, "version": version, "name": name}
            else:
                item = {"opaque_row_hex": row.hex()}
            decoded.append(item)
        result[str(kind)] = sorted(decoded, key=lambda row: json.dumps(row, sort_keys=True))
    return result


def extended_semantics(data: bytes) -> dict:
    result = {}
    for kind, rows in pdb_rows(data).items():
        decoded = []
        for row in rows:
            if kind == 3:
                item = {"subtype": struct.unpack_from("<H", row)[0],
                        "reserved": list(struct.unpack_from("<2I", row, 4)),
                        "parent": struct.unpack_from("<I", row, 12)[0],
                        "sequence": struct.unpack_from("<I", row, 16)[0],
                        "id": struct.unpack_from("<I", row, 20)[0],
                        "flags": row[24:28].hex(),
                        "strings": [device_string(row, offset)[0] for offset in (28, row[29], row[30])]}
            else:
                item = {"opaque_row_hex": row.hex()}
            decoded.append(item)
        result[str(kind)] = sorted(decoded, key=lambda row: json.dumps(row, sort_keys=True))
    return result


def compare(workspace: Path, reference: Path, candidate: Path, audio: Path) -> dict:
    workspace = workspace.resolve(strict=True)
    for path in (reference, candidate, audio):
        if not path.resolve(strict=True).is_relative_to(workspace) or path.is_symlink():
            raise ValueError("Inputs must be explicit directories inside the offline workspace")
    if workspace.is_relative_to("/Volumes"):
        raise ValueError("Copy references locally first")
    capture = json.loads((reference / "capture.json").read_text())
    media = json.loads((audio / "capture.json").read_text())["audio"]
    expected = {name: {**row, "path": reference / name} for name, row in capture["metadata_files"].items()}
    expected.update({name: {**row, "path": audio / name} for name, row in media.items()})
    for name, row in expected.items():
        bound = reference if name in capture["metadata_files"] else audio
        if Path(name).is_absolute() or ".." in Path(name).parts or not row["path"].resolve().is_relative_to(bound.resolve()):
            raise ValueError("Reference entry escaped its captured directory")
    actual = {}
    for path in candidate.rglob("*"):
        if path.is_symlink():
            raise ValueError("Candidate symlink refused")
        if path.is_file():
            actual[path.relative_to(candidate).as_posix()] = path
    files = {}
    for name, row in expected.items():
        if row["path"].is_symlink() or sha256(row["path"]) != row["sha256"]:
            raise ValueError("Reference changed: " + name)
        path = actual.get(name)
        digest = sha256(path) if path else None
        files[name] = {"same_bytes": digest == row["sha256"], "reference_sha256": row["sha256"],
                       "candidate_sha256": digest, "reference_size": row["size"],
                       "candidate_size": path.stat().st_size if path else None}
    databases = {}
    for name in ("export.pdb", "exportExt.pdb"):
        a = (reference / "PIONEER/rekordbox" / name).read_bytes()
        b = (candidate / "PIONEER/rekordbox" / name).read_bytes()
        if name == "export.pdb":
            x, y = classic_semantics(a), classic_semantics(b)
        else:
            x, y = extended_semantics(a), extended_semantics(b)
        databases[name] = {"same_decoded_rows": x == y,
                           "different_tables": {key: {"reference": x.get(key), "candidate": y.get(key)}
                                                for key in set(x) | set(y) if x.get(key) != y.get(key)}}
    key = workspace / "rbxport-kit-b739d369/crates/rbl-onelibrary/src/key.rs"
    a = sql_rows(reference / "PIONEER/rekordbox/exportLibrary.db", key)
    b = sql_rows(candidate / "PIONEER/rekordbox/exportLibrary.db", key)
    databases["exportLibrary.db"] = {"same_decoded_rows": a == b,
        "different_tables": {key: {"reference": a["tables"].get(key), "candidate": b["tables"].get(key)}
                             for key in set(a["tables"]) | set(b["tables"]) if a["tables"].get(key) != b["tables"].get(key)},
        "same_columns": a["columns"] == b["columns"], "same_indexes": a["indexes"] == b["indexes"]}
    databases["exportLibrary.db"].update(same_declarations=a["declarations"] == b["declarations"],
        same_views_and_triggers=a["views_and_triggers"] == b["views_and_triggers"],
        same_protocol=a["protocol"] == b["protocol"])
    extra = sorted(set(actual) - set(expected))
    all_database_rows_match = all(row["same_decoded_rows"] for row in databases.values())
    payload_match = all(row["same_bytes"] for name, row in files.items()
                        if name not in {"PIONEER/rekordbox/" + name for name in databases})
    return {"reference": str(reference), "candidate": str(candidate),
            "strict_byte_match": all(row["same_bytes"] for row in files.values()) and not extra,
            "native_equivalence_match": payload_match and all_database_rows_match and not extra,
            "non_database_files_match": payload_match,
            "expected_files": len(files), "byte_identical_files": sum(row["same_bytes"] for row in files.values()),
            "files": files, "extra_files": extra, "databases": databases,
            "all_database_rows_match": all_database_rows_match,
            "hardware_acceptance": False,
            "scope": "Renderer fixture, not installed SoundBox/live source adapter acceptance. PDB physical row slots/padding/headers remain covered by the raw comparison, not normalized decoded rows."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gate", choices=("native-equivalence", "strict-bytes"), required=True)
    args = parser.parse_args()
    if not args.report.parent.resolve().is_relative_to(args.workspace.resolve()) or args.report.exists():
        parser.error("Report must be new and inside the explicit offline workspace")
    result = compare(args.workspace, args.reference, args.candidate, args.audio)
    result["gate"] = args.gate
    result["gate_passed"] = result["native_equivalence_match"] if args.gate == "native-equivalence" else result["strict_byte_match"]
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("gate", "gate_passed", "strict_byte_match", "expected_files", "byte_identical_files",
                     "extra_files", "all_database_rows_match")}, indent=2))
    if not result["gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

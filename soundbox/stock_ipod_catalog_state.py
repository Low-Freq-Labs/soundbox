"""Bounded disposable catalog correspondence; never media ownership/proof."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from .state_io import atomic_bytes
from .stock_ipod_playlist_binding import PlaylistBinding, playlist_binding_from_dict

FORMAT = "soundbox-stock-catalog-witness-v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 16_384


@dataclass(frozen=True)
class CatalogWitnessRow:
    local_id: str
    native_id: str
    uuid: str
    source_path: str
    dbid: int
    device_track_id: int
    location: str
    metadata: tuple[tuple[str, str | int], ...]
    artwork_key: str = ""


@dataclass(frozen=True)
class StockCatalogWitness:
    source: tuple[str, str, str]
    device_key: str
    database_sha256: str
    rows: tuple[CatalogWitnessRow, ...]
    artwork_database_sha256: str = ""
    bindings: tuple[PlaylistBinding, ...] = ()

    def encode(self) -> bytes:
        raw = json.dumps(dict(format=FORMAT, **asdict(self)), allow_nan=False,
                         separators=(",", ":"), ensure_ascii=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("Catalog witness exceeds its supported size")
        # One parser contract covers both write and read admission.
        self.decode(raw)
        return raw

    @classmethod
    def decode(cls, raw: bytes) -> StockCatalogWitness:
        if len(raw) > MAX_BYTES:
            raise ValueError("Catalog witness exceeds its supported size")
        data = json.loads(raw)
        if (set(data) not in ({"format", "source", "device_key", "database_sha256", "rows", "artwork_database_sha256"},
                             {"format", "source", "device_key", "database_sha256", "rows", "artwork_database_sha256", "bindings"})
                or data["format"] != FORMAT or not isinstance(data["source"], list)
                or len(data["source"]) != 3 or any(type(v) is not str for v in data["source"])
                or not Path(data["source"][0]).is_absolute()
                or type(data["device_key"]) is not str or not data["device_key"]
                or type(data["database_sha256"]) is not str
                or len(data["database_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in data["database_sha256"])
                or type(data["artwork_database_sha256"]) is not str
                or data["artwork_database_sha256"] and (
                    len(data["artwork_database_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in data["artwork_database_sha256"]))
                or not isinstance(data["rows"], list) or len(data["rows"]) > MAX_ROWS):
            raise ValueError("Invalid catalog witness")
        rows = []; seen = set()
        for row in data["rows"]:
            if (not isinstance(row, dict) or set(row) != set(CatalogWitnessRow.__dataclass_fields__)
                    or any(type(row[k]) is not str for k in (
                        "local_id", "native_id", "uuid", "source_path", "location", "artwork_key"))
                    or not row["local_id"] or not Path(row["source_path"]).is_absolute()
                    or any(type(row[k]) is not int or row[k] <= 0 for k in ("dbid", "device_track_id"))
                    or not isinstance(row["metadata"], list)
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or pair[0] not in {"title", "artist", "album", "genre", "comments", "year", "bpm"}
                           or type(pair[1]) not in (str, int) for pair in row["metadata"])
                    or len({pair[0] for pair in row["metadata"]}) != len(row["metadata"])):
                raise ValueError("Invalid catalog witness row")
            if ({pair[0] for pair in row['metadata']} != {'title', 'artist', 'album', 'genre', 'comments', 'year', 'bpm'}
                    or any((type(value) is not int or not 0 <= value <= 65535)
                           if field in {'year', 'bpm'} else
                           (type(value) is not str or '\0' in value or len(value) > 4096)
                           for field, value in row['metadata'])):
                raise ValueError("Invalid catalog witness metadata")
            identity = (row["local_id"], row["dbid"])
            if identity in seen:
                raise ValueError("Duplicate catalog witness row")
            seen.add(identity)
            rows.append(CatalogWitnessRow(**{**row, "metadata": tuple(map(tuple, row["metadata"]))}))
        bindings = data.get('bindings', [])
        if not isinstance(bindings, list) or len(bindings) > MAX_ROWS:
            raise ValueError('Invalid catalog playlist bindings')
        parsed_bindings = tuple(playlist_binding_from_dict(binding) for binding in bindings)
        if len({binding.key for binding in parsed_bindings}) != len(parsed_bindings):
            raise ValueError('Duplicate catalog playlist binding')
        return cls(tuple(data["source"]), data["device_key"], data["database_sha256"], tuple(rows),
                   data["artwork_database_sha256"], parsed_bindings)


class StockCatalogWitnessStore:
    """Separate optional cache; a cache miss never changes device authority."""
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path_for(self, device_key: str) -> Path:
        return self.root / (hashlib.sha256(device_key.encode()).hexdigest() + ".json")

    def load(self, device_key: str) -> StockCatalogWitness | None:
        path = self.path_for(device_key)
        try:
            if self.root.is_symlink() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
                return None
            with path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            witness = StockCatalogWitness.decode(raw)
            return witness if witness.device_key == device_key else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(self, witness: StockCatalogWitness) -> None:
        raw = witness.encode()
        if self.root.is_symlink() or self.path_for(witness.device_key).is_symlink():
            raise ValueError("Catalog witness cache cannot follow a symlink")
        atomic_bytes(self.path_for(witness.device_key), raw)

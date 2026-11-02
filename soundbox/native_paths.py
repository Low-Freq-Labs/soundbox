"""Bounded, durable proofs for an existing native path through a local alias.

These proofs preserve the raw native path and never authorize moving media or
rewriting a content row. Capture/revalidation belongs in the source worker.
"""
from dataclasses import asdict, dataclass
from pathlib import Path

from .native_media import epoch
from .rekordbox_write import ConflictError, WriteProofError


@dataclass(frozen=True)
class AliasProof:
    native_id: str
    native_uuid: str
    path: str
    canonical: str
    epoch: tuple[int, ...]

    def validate(self):
        from .native_registration_plan import _native_id, _uuid
        _native_id(self.native_id); _uuid(self.native_uuid)
        for value in (self.path, self.canonical):
            if (not isinstance(value, str) or '\0' in value or not Path(value).is_absolute()
                    or str(Path(value)) != value or '..' in Path(value).parts):
                raise WriteProofError('Explicit native alias paths required')
        if (self.path == self.canonical or len(self.epoch) != 5
                or any(type(n) is not int or n < 0 for n in self.epoch)
                or self.epoch[1] <= 0 or self.epoch[2] <= 0):
            raise WriteProofError('Invalid native alias proof')

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise WriteProofError('Unknown native alias proof')
        proof = cls(**{**value, 'epoch': tuple(value['epoch'])})
        proof.validate()
        return proof

    def revalidate(self):
        self.validate()
        path, canonical = Path(self.path), Path(self.canonical)
        if (path.resolve(strict=True) != canonical or canonical.resolve(strict=True) != canonical
                or epoch(path.stat()) != self.epoch or epoch(canonical.stat()) != self.epoch):
            raise ConflictError('Native media alias changed; preserve both identities')


def capture_aliases(rows, audio):
    """Resolve paths only, without opening/hashing/hydrating unrelated media."""
    result = []
    for row in rows:
        raw = row.get('FolderPath')
        if not isinstance(raw, str) or not raw or raw == audio.path:
            continue
        try:
            if Path(raw).resolve(strict=True) != Path(audio.path):
                continue
        except (OSError, RuntimeError):
            continue
        proof = AliasProof(row['ID'], row['UUID'], raw, audio.path, audio.epoch)
        proof.revalidate()
        result.append(asdict(proof))
    return result


def validate_track_path(track):
    """Worker guard: a saved raw native alias must still name this canonical file."""
    if track.source_native_path and (track.native_path.resolve(strict=True)!=track.path
            or track.path.resolve(strict=True)!=track.path):
        raise ConflictError('Native media alias changed; refresh before editing')

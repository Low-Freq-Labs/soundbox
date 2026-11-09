"""Small deterministic audio builders for disposable test workspaces."""
from pathlib import Path
import struct


def wave(path: Path, *, rate: int = 44100, seconds: int = 1) -> None:
    """Write the shared bare stereo PCM16 registration reference."""
    data = b'\0' * (rate * 4 * seconds)
    path.write_bytes(struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36+len(data), b'WAVE',
        b'fmt ', 16, 1, 2, rate, rate*4, 4, 16, b'data', len(data))+data)

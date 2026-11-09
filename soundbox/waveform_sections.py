"""Pure section codec for the SoundBox waveform cache container.

Section ordering, Zstandard parameters and SHA-256 records are part of the
existing V4/V5 byte contract. Source identity, header validation and atomic
publication remain owned by track_analysis; this codec performs no file I/O.
"""
import hashlib


def encode_sections(sections: dict[str, bytes]) -> tuple[dict, bytes]:
    import zstandard as zstd

    compressor = zstd.ZstdCompressor(level=6, threads=0)
    manifest_sections = {}
    payload = bytearray()
    for name, raw in sections.items():
        compressed = compressor.compress(raw)
        manifest_sections[name] = {
            "offset": len(payload),
            "size": len(compressed),
            "raw_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        payload.extend(compressed)
    return manifest_sections, bytes(payload)


def decode_sections(raw: bytes, manifest: dict) -> dict[str, bytes] | None:
    import zstandard as zstd

    decoder = zstd.ZstdDecompressor()
    sections = {}
    for name, row in manifest.get("sections", {}).items():
        try:
            start = int(row["offset"])
            end = start + int(row["size"])
            unpacked = decoder.decompress(
                raw[start:end], max_output_size=int(row["raw_size"]))
            if (len(unpacked) != int(row["raw_size"])
                    or hashlib.sha256(unpacked).hexdigest() != row["sha256"]):
                return None
            sections[name] = unpacked
        except (KeyError, TypeError, ValueError, zstd.ZstdError):
            return None
    return sections

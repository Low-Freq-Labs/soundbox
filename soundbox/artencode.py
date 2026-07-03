"""Device-safe cover-art encoding.

Rockbox renders BASELINE JPEG only — progressive/multi-scan JPEG and PNG are
valid files that show BLANK on the iPod. Everything stored in the art cache
(and therefore everything written to a device as cover.jpg) is normalized to
baseline JPEG here. SoundCloud's CDN happens to serve baseline today, but
manual paste-URL / choose-file art can be anything, so this is enforced at
the cache boundary rather than trusted.
"""

from __future__ import annotations

import io

# Rockbox screens are small (iPod Video = 320x240); cap huge manual images so
# re-encodes stay light and covers load fast on-device. 500 matches SoundCloud's
# t500x500, which already renders fine.
MAX_SIDE = 500
_QUALITY = 90

_SOF_BASELINE = (0xC0, 0xC1)  # sequential DCT
_SOF_PROGRESSIVE = (0xC2,)


def kind(data: bytes) -> str:
    """'baseline' | 'progressive' | 'png' | 'unknown' — by header/marker scan,
    no decode. Baseline is the only kind Rockbox renders as album art."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] != b"\xff\xd8":
        return "unknown"
    i, n = 2, len(data)
    while i < n - 3:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in _SOF_BASELINE:
            return "baseline"
        if m in _SOF_PROGRESSIVE:
            return "progressive"
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:  # markers with no length field
            i += 2
            continue
        i += 2 + ((data[i + 2] << 8) | data[i + 3])
    return "unknown"


def is_device_safe(data: bytes) -> bool:
    return kind(data) == "baseline"


def to_baseline_jpeg(data: bytes) -> bytes | None:
    """Re-encode any image bytes to baseline JPEG (RGB, alpha flattened to
    white, capped at MAX_SIDE). None if the bytes can't be decoded."""
    from PIL import Image

    try:
        img: Image.Image = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.mode in ("RGBA", "LA", "P", "PA"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=_QUALITY, optimize=True, progressive=False)
    return out.getvalue()


def ensure_device_safe(data: bytes) -> bytes:
    """data if already baseline JPEG, else the baseline re-encode. Falls back
    to the original bytes if the re-encode fails (better a maybe-blank cover
    on-device than none in the cache)."""
    if is_device_safe(data):
        return data
    return to_baseline_jpeg(data) or data

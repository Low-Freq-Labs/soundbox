"""Device-safe cover-art encoding.

Rockbox renders BASELINE JPEG only — progressive/multi-scan JPEG and PNG are
valid files that show BLANK on the iPod. Everything stored in the art cache
(and therefore every canonical cached JPEG) is normalized to baseline JPEG
here. SoundCloud's CDN happens to serve baseline today, but
manual paste-URL / choose-file art can be anything, so this is enforced at
the cache boundary rather than trusted.
"""

from __future__ import annotations

import io

# Canonical curated images remain at the established 500px ceiling.  The active
# iPod Video AdwaitaPod Dark Simplified theme requests exact 160x160 playback
# and 116x116 home-screen views.  Disposable Rockbox derivatives match both
# viewports instead of asking the PortalPlayer target to decode unused pixels.
MAX_SIDE = 500
_QUALITY = 90
ROCKBOX_COVER_SIDE = 160
ROCKBOX_COVER_SIDES = (160, 116)
ROCKBOX_FALLBACK_SIDE = 160
ROCKBOX_ART_SIDES = tuple(dict.fromkeys(
    (*ROCKBOX_COVER_SIDES, ROCKBOX_FALLBACK_SIDE)))

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


def to_rockbox_cover(
        data: bytes, side: int = ROCKBOX_COVER_SIDE) -> bytes | None:
    """Return the canonical Rockbox cover derivative.

    The curated cache remains the source of truth.  This always decodes and
    re-encodes a separate exact-size 8-bit paletted bitmap so Rockbox never has
    to handle a large, progressive, grayscale, CMYK, or oddly-sized source
    image.  The installed theme uses this same uncompressed BMP representation;
    it cuts device reads by roughly two thirds versus a 24-bit BMP while staying
    CPU-cheap for the PortalPlayer target.  The exact-size filename used by the
    exporter also lets Rockbox find it on its first album-art lookup pass.
    Current curated artwork is square; non-square future input is centered on
    a white square rather than cropped.
    """
    from PIL import Image, ImageOps

    if not isinstance(side, int) or not 1 <= side <= 240:
        raise ValueError(f"Unsupported Rockbox cover side: {side}")
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
    img.thumbnail(
        (side, side), Image.Resampling.LANCZOS)
    img = ImageOps.pad(
        img,
        (side, side),
        method=Image.Resampling.LANCZOS,
        color=(255, 255, 255),
        centering=(0.5, 0.5),
    )
    img = img.quantize(
        colors=256,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    )
    out = io.BytesIO()
    img.save(out, "BMP")
    return out.getvalue()

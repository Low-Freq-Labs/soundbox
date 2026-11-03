"""Bounded read-only MPEG-1 Layer III framing and seek facts; no source publisher.

The observed native CBR PVBR contains 400 zero offsets and an untrimmed audio
sample count. Two independently generated 44.1kHz Xing VBR references establish
the narrow research bucket rule below. Callers supply explicit captured paths;
neither constructor grants general format acceptance or source-write authority.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import struct

from soundbox.native_media import epoch
from soundbox.rekordbox_write import ConflictError, WriteProofError

MAX_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 250_000
MAX_TAG_BYTES = 16 * 1024 * 1024
BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
RATES = (44100, 48000, 32000)


@dataclass(frozen=True)
class MP3Scan:
    path: str
    source_epoch: tuple[int, ...]
    sha256: str
    sample_rate: int
    channels: int
    frame_offsets: tuple[int, ...]
    frame_lengths: tuple[int, ...]
    bitrates: tuple[int, ...]
    audio_start: int
    audio_frames: int
    info_kind: str | None
    mpeg_end: int

    @property
    def sample_count(self):
        return self.audio_frames * 1152

    @property
    def cbr(self):
        return len(set(self.bitrates)) == 1 and self.info_kind != 'Xing'


def _validate_scan(scan):
    if not isinstance(scan, MP3Scan):
        raise WriteProofError('Explicit inspected MP3 frame facts required')
    if (type(scan.path) is not str or not Path(scan.path).is_absolute()
            or '..' in Path(scan.path).parts or '\0' in scan.path
            or type(scan.sha256) is not str or len(scan.sha256) != 64
            or any(c not in '0123456789abcdef' for c in scan.sha256)
            or type(scan.source_epoch) is not tuple or len(scan.source_epoch) != 5
            or any(type(n) is not int or n < 0 for n in scan.source_epoch)
            or not 0 < scan.source_epoch[2] <= MAX_BYTES
            or type(scan.sample_rate) is not int or scan.sample_rate not in (44100, 48000)
            or type(scan.channels) is not int or scan.channels != 2
            or scan.info_kind not in (None, 'Info', 'Xing')
            or any(type(v) is not tuple for v in (scan.frame_offsets, scan.frame_lengths, scan.bitrates))
            or not 0 < len(scan.frame_offsets) <= MAX_FRAMES
            or len(scan.frame_offsets) != len(scan.frame_lengths)
            or type(scan.audio_frames) is not int or scan.audio_frames != len(scan.bitrates)
            or scan.audio_frames != len(scan.frame_offsets) - int(scan.info_kind is not None)
            or scan.audio_frames < 1
            or any(type(n) is not int or n < 0 for n in scan.frame_offsets)
            or any(type(n) is not int or n < 1 for n in scan.frame_lengths)
            or any(type(n) is not int or n not in BITRATES[1:] for n in scan.bitrates)
            or type(scan.audio_start) is not int or type(scan.mpeg_end) is not int):
        raise WriteProofError('Malformed inspected MP3 frame facts')
    skip = int(scan.info_kind is not None)
    if (scan.audio_start != scan.frame_offsets[skip]
            or scan.frame_offsets[0] > MAX_TAG_BYTES + 10
            or scan.frame_offsets[-1] + scan.frame_lengths[-1] != scan.mpeg_end
            or scan.source_epoch[2] - scan.mpeg_end not in (0, 128)
            or any(a + size != b for a, size, b in zip(scan.frame_offsets, scan.frame_lengths, scan.frame_offsets[1:]))
            or any(size not in (144000 * bitrate // scan.sample_rate, 144000 * bitrate // scan.sample_rate + 1)
                   for size, bitrate in zip(scan.frame_lengths[skip:], scan.bitrates))):
        raise WriteProofError('Inspected MP3 offsets and framing disagree')


def _read(path, limit):
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts or path != path.resolve(strict=True)
            or path.is_symlink()):
        raise WriteProofError('Explicit canonical MP3 research path required')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise WriteProofError('MP3 research byte budget exceeded or file is not regular')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as handle:
        if epoch(os.fstat(handle.fileno())) != epoch(before):
            raise ConflictError('MP3 research input changed before inspection')
        raw = handle.read(limit + 1)
        if (len(raw) != before.st_size or epoch(os.fstat(handle.fileno())) != epoch(before)
                or epoch(path.stat()) != epoch(before) or path != path.resolve(strict=True)):
            raise ConflictError('MP3 research input changed during inspection')
    return path, before, raw


def _tag_start(raw):
    if raw[:3] != b'ID3':
        return 0
    if len(raw) < 10 or raw[3] not in (3, 4) or raw[4] != 0 or raw[5] != 0 or any(n & 128 for n in raw[6:10]):
        raise WriteProofError('Only plain ID3v2.3/v2.4 research tags are supported')
    size = 0
    for value in raw[6:10]:
        size = size * 128 + value
    if size > MAX_TAG_BYTES or 10 + size > len(raw):
        raise WriteProofError('Truncated or oversized ID3v2 research tag')
    return 10 + size


def _header(raw, offset):
    value = int.from_bytes(raw[offset:offset + 4], 'big')
    if (len(raw[offset:offset + 4]) != 4 or value >> 21 != 0x7ff or value >> 19 & 3 != 3
            or value >> 17 & 3 != 1 or value >> 12 & 15 in (0, 15)
            or value >> 10 & 3 == 3 or value & 3 == 2):
        raise WriteProofError('Unsupported or malformed MPEG-1 Layer III frame')
    rate = RATES[value >> 10 & 3]
    channels = 1 if value >> 6 & 3 == 3 else 2
    if rate not in (44100, 48000) or channels != 2:
        raise WriteProofError('MP3 seek research supports stereo 44.1/48kHz only')
    bitrate = BITRATES[value >> 12 & 15]
    length = 144000 * bitrate // rate + (value >> 9 & 1)
    crc_bytes = 0 if value >> 16 & 1 else 2
    return rate, channels, bitrate, length, crc_bytes


def _info(raw, start, length, crc_bytes):
    location = start + 4 + crc_bytes + 32
    kind = raw[location:location + 4]
    if kind not in (b'Info', b'Xing'):
        return None
    cursor = location + 8
    if cursor > start + length:
        raise WriteProofError('Truncated MPEG information frame')
    flags = int.from_bytes(raw[location + 4:cursor], 'big')
    if flags & ~15 or flags & 3 != 3:
        raise WriteProofError('MP3 information frame requires exact frame/byte counts')
    sizes = [(1, 4), (2, 4), (4, 100), (8, 4)]
    fields = {}
    for flag, size in sizes:
        if flags & flag:
            if cursor + size > start + length:
                raise WriteProofError('Truncated MPEG information field')
            fields[flag] = raw[cursor:cursor + size]
            cursor += size
    return kind.decode('ascii'), int.from_bytes(fields[1], 'big'), int.from_bytes(fields[2], 'big')


def scan_mp3(path, *, expected_sha256=None, max_bytes=MAX_BYTES, max_frames=MAX_FRAMES):
    """Inspect one stable explicit capture without decoding or altering its bytes."""
    if (type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES
            or type(max_frames) is not int or not 0 < max_frames <= MAX_FRAMES):
        raise WriteProofError('Invalid MP3 research budget')
    path, before, raw = _read(path, max_bytes)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ConflictError('MP3 research capture does not match its expected digest')
    start = _tag_start(raw)
    end = len(raw) - 128 if len(raw) >= 128 and raw[-128:-125] == b'TAG' else len(raw)
    cursor = start
    offsets: list[int] = []
    lengths: list[int] = []
    bitrates: list[int] = []
    framing = None
    info = None
    while cursor < end:
        if len(offsets) >= max_frames:
            raise WriteProofError('MP3 research frame budget exceeded')
        rate, channels, bitrate, length, crc_bytes = _header(raw, cursor)
        if cursor + length > end:
            raise WriteProofError('Truncated MPEG frame or unsupported trailing bytes')
        if framing is None:
            framing = rate, channels
            info = _info(raw, cursor, length, crc_bytes)
        elif framing != (rate, channels):
            raise WriteProofError('MPEG sample rate or channel framing changed')
        elif _info(raw, cursor, length, crc_bytes) is not None:
            raise WriteProofError('Repeated MPEG information frame')
        offsets.append(cursor)
        lengths.append(length)
        bitrates.append(bitrate)
        cursor += length
    skip = int(info is not None)
    if framing is None or len(offsets) <= skip:
        raise WriteProofError('MP3 capture contains no audio frames')
    if info and (info[1] != len(offsets) - 1 or info[2] != end - start):
        raise WriteProofError('MPEG information frame counts disagree with captured framing')
    return MP3Scan(str(path), epoch(before), digest, *framing, tuple(offsets), tuple(lengths),
        tuple(bitrates[skip:]), offsets[skip], len(offsets) - skip, info[0] if info else None, end)


def cbr_pvbr(scan):
    """Construct the observed tagless-CBR section; no general VBR authoring rule."""
    _validate_scan(scan)
    if not scan.cbr or scan.info_kind is not None:
        raise WriteProofError('Only the observed CBR profile without Info/Xing can be reconstructed')
    if not 0 < scan.sample_count < 2**32:
        raise WriteProofError('Native PVBR sample-count overflow')
    return struct.pack('>4sIII', b'PVBR', 16, 1620, 0) + bytes(400 * 4) + struct.pack('>I', scan.sample_count)


def vbr_pvbr(scan):
    """Reconstruct only the observed MPEG-1/stereo/44.1kHz Xing VBR profile.

    Independent generated native examples match every bucket exactly. Offsets exclude the leading Xing frame and retain the
    untrimmed sample endpoint. Info, tagless VBR, constant-rate Xing and 48kHz
    generation remain unproven. This research function has no source publisher.
    """
    _validate_scan(scan)
    if (scan.info_kind != 'Xing' or scan.sample_rate != 44100 or scan.channels != 2
            or len(set(scan.bitrates)) < 2):
        raise WriteProofError('Only the observed stereo 44.1kHz Xing VBR research profile can be reconstructed')
    if not 0 < scan.sample_count < 2**32:
        raise WriteProofError('Native PVBR sample-count overflow')
    offsets: list[int] = []
    for index in range(400):
        audio_frame = max(0, (index + 1) * scan.audio_frames // 400 - 8)
        if not 0 <= audio_frame < scan.audio_frames:
            raise WriteProofError('VBR research bucket exceeds its captured audio frames')
        frame_index = audio_frame + 1  # The scanner binds exactly one leading Xing frame.
        if not 1 <= frame_index < len(scan.frame_offsets):
            raise WriteProofError('VBR research bucket includes the information frame or exceeds its capture')
        offset = scan.frame_offsets[frame_index] - scan.audio_start
        if not 0 <= offset < 2**32 or offsets and offset < offsets[-1]:
            raise WriteProofError('VBR research bucket offsets are invalid')
        offsets.append(offset)
    return struct.pack('>4sIII401I', b'PVBR', 16, 1620, 0, *offsets, scan.sample_count)


def compare_pvbr(scan, section):
    """Report exact offset boundaries; matching does not grant publication authority."""
    _validate_scan(scan)
    if (type(section) is not bytes or len(section) != 1620
            or section[:16] != struct.pack('>4sIII', b'PVBR', 16, 1620, 0)):
        raise WriteProofError('Explicit exact native PVBR section required')
    words = struct.unpack('>401I', section[16:])
    if words[-1] != scan.sample_count:
        raise WriteProofError('Native PVBR sample count differs from captured audio frames')
    first = 1 if scan.info_kind is not None else 0
    lookup = {offset: index - first for index, offset in enumerate(scan.frame_offsets) if index >= first}
    rows = [dict(index=i, relative_offset=value, absolute_offset=scan.audio_start + value,
                 audio_frame=lookup.get(scan.audio_start + value)) for i, value in enumerate(words[:-1])]
    matches = sum(row['audio_frame'] is not None for row in rows)
    reconstructed = cbr_pvbr(scan) == section if scan.cbr and scan.info_kind is None else None
    vbr_profile = (scan.info_kind == 'Xing' and scan.sample_rate == 44100
                   and scan.channels == 2 and len(set(scan.bitrates)) > 1)
    vbr_reconstructed = vbr_pvbr(scan) == section if vbr_profile else None
    return dict(source_sha256=scan.sha256, pvbr_sha256=hashlib.sha256(section).hexdigest(),
        sample_count=scan.sample_count, audio_frames=scan.audio_frames, audio_start=scan.audio_start,
        sample_rate=scan.sample_rate, channels=scan.channels, info_kind=scan.info_kind,
        monotonic=all(a <= b for a, b in zip(words[:399], words[1:400])),
        frame_boundary_matches=matches, all_frame_boundaries=matches == 400,
        cbr_reconstruction_exact=reconstructed, vbr_generation_supported=False,
        research_vbr_reconstruction_exact=vbr_reconstructed,
        source_write_authority=False, rows=rows)

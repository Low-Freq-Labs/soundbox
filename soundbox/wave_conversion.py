"""Recoverable import-only float32 WAVE -> PCM24, explicitly requested by Sonny.

This exception never applies to registered native audio or to exporters. The
caller owns the shared source lane and rechecks the native catalog before the
atomic replacement. Exact originals and receipts remain outside the music tree.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from uuid import UUID
from fractions import Fraction

from .analysis import identity, result_key, validate_result
from .media_probe import read_media_header
from .source_acquisition import SourceContract, acquire_source
from .state_io import atomic_bytes, sync_dir

FORMAT = 'soundbox-float-wave-to-pcm24-v1'
KEY = 'wave_conversions'
SCALE = 8388608


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):h.update(block)
    return h.hexdigest()


def _regular(path):
    s = Path(path).lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid():
        raise ValueError('WAVE conversion requires a regular file with one link')
    return s


def _current_digest(path):
    before = identity(_regular(path)); value = _sha(path)
    if before != identity(_regular(path)) or Path(path).resolve(strict=True) != path:
        raise ValueError('WAVE changed during verification')
    return value, before


def validate_receipt(receipt):
    required={'format','operation_id','track_id','path','before_sha256','before_epoch','after_sha256',
              'size','stage','frames','sample_rate','channels','peak','gain','gain_db','max_error','clipped_samples'}
    if (not isinstance(receipt,dict) or set(receipt) not in (required,required|{'epoch'})
            or receipt.get('format') != FORMAT):raise ValueError('Unknown WAVE conversion receipt')
    for field in ('operation_id','track_id','path','stage'):
        if not isinstance(receipt[field],str) or not receipt[field] or '\0' in receipt[field]:
            raise ValueError('Invalid WAVE conversion identity')
    if str(UUID(receipt['operation_id'])) != receipt['operation_id']:
        raise ValueError('Invalid WAVE conversion operation')
    path,stage=Path(receipt['path']),Path(receipt['stage'])
    if (not path.is_absolute() or '..' in path.parts or stage.parent!=path.parent
            or not stage.name.startswith('.soundbox-wave-') or '..' in stage.parts):
        raise ValueError('Invalid WAVE conversion paths')
    for field in ('before_sha256','after_sha256'):
        value=receipt[field]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid WAVE conversion digest')
    for field in ('before_epoch','epoch'):
        if field not in receipt:continue
        value=receipt[field]
        if not isinstance(value,list) or len(value)!=5 or any(type(n) is not int or n < 0 for n in value):
            raise ValueError('Invalid WAVE conversion epoch')
    for field in ('frames','sample_rate','channels','size','clipped_samples'):
        if type(receipt[field]) is not int:raise ValueError('Invalid WAVE conversion framing')
    if (not 8000<=receipt['sample_rate']<=192000 or not 1<=receipt['channels']<=8
            or not 0<receipt['frames']<=receipt['sample_rate']*86400 or receipt['size']<44
            or not 0<=receipt['clipped_samples']<=receipt['frames']*receipt['channels']
            or 'epoch' in receipt and receipt['epoch'][2]!=receipt['size']):
        raise ValueError('Invalid WAVE conversion frame mapping')
    for field in ('peak','gain','gain_db','max_error'):
        if type(receipt[field]) not in (int,float) or not math.isfinite(receipt[field]):
            raise ValueError('Invalid WAVE conversion signal proof')
    if (receipt['peak']<0 or not 0<receipt['gain']<=1 or not 0<=receipt['max_error']<=1/SCALE
            or abs(receipt['gain_db']-20*math.log10(receipt['gain']))>1e-9):
        raise ValueError('Invalid WAVE conversion gain/rounding proof')


def layout(path):
    """Keep the complete ordered RIFF directory, including repeated tag chunks."""
    path = Path(path)
    if path.suffix.lower() != '.wav':return None
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE':return None
        if struct.unpack_from('<I', header, 4)[0]+8 != size:raise ValueError('Incomplete WAVE container')
        chunks = []; position = 12
        for _ in range(256):
            if position == size:break
            if position+8 > size:raise ValueError('Truncated WAVE chunk')
            stream.seek(position); raw = stream.read(8); length = struct.unpack_from('<I', raw, 4)[0]
            end = position+8+length+(length & 1)
            if end > size:raise ValueError('WAVE chunk extends past EOF')
            chunks.append((raw[:4], position+8, length)); position = end
        if position != size:raise ValueError('WAVE chunk directory exceeds the header budget')
        fmts = [c for c in chunks if c[0] == b'fmt ']; data = [c for c in chunks if c[0] == b'data']
        if len(fmts) != 1 or len(data) != 1:raise ValueError('Ambiguous WAVE audio chunks')
        _, offset, length = fmts[0]
        if not 16 <= length <= 40:raise ValueError('Unsupported WAVE format header')
        stream.seek(offset); fmt = stream.read(length)
        tag, channels, rate, byte_rate, align, bits = struct.unpack('<HHIIHH', fmt[:16])
        if tag == 65534:
            if length != 40 or struct.unpack_from('<HH', fmt, 16) != (22, bits):
                raise ValueError('Invalid extensible WAVE framing')
            if fmt[24:] != bytes.fromhex('0300000000001000800000aa00389b71'):return None
        elif tag != 3:return None
        elif length not in (16,18) or length == 18 and fmt[16:] != b'\0\0':
            raise ValueError('Unsupported float WAVE extension')
        if (bits != 32 or not 1 <= channels <= 8 or not 8000 <= rate <= 192000
                or align != channels*4 or byte_rate != rate*align or data[0][2] % align):
            raise ValueError('Unsupported float32 WAVE framing')
        frames = data[0][2]//align
        if not 0 < frames/rate <= 86400:raise ValueError('Unsupported WAVE duration')
        return dict(chunks=chunks, channels=channels, rate=rate, frames=frames, data=data[0])


def _blocks(stream, info):
    import numpy as np
    _, offset, remaining = info['data']; stream.seek(offset)
    while remaining:
        block_size = (1024*1024)//(info['channels']*4)*(info['channels']*4)
        raw = stream.read(min(block_size, remaining))
        if not raw or len(raw) % 4:raise ValueError('Truncated float WAVE samples')
        remaining -= len(raw)
        yield np.frombuffer(raw, dtype='<f4').astype(np.float64)


def render(source, target, *, reduce_gain=False, clip_overs=False):
    """Stream samples, preserve non-format/data chunks byte for byte and verify."""
    import numpy as np
    info = layout(source)
    if info is None:raise ValueError('Expected a float32 WAVE')
    peak = 0.0; overrange_samples = 0
    with Path(source).open('rb') as stream:
        for samples in _blocks(stream, info):
            if not np.isfinite(samples).all():raise ValueError('Float WAVE contains non-finite samples')
            peak = max(peak, float(np.max(np.abs(samples))))
            overrange_samples += int(np.count_nonzero(np.abs(samples) > 1))
    if peak > 1 and not (reduce_gain or clip_overs):
        raise ValueError(f'Float WAVE peaks at +{20*math.log10(peak):.2f} dB; gain reduction is required to avoid clipping')
    gain = ((SCALE-1)/SCALE)/peak if peak > 1 and reduce_gain else 1.0
    chunks = info['chunks']; data_length = info['frames']*info['channels']*3
    total = 12+sum(8+(16 if k == b'fmt ' else data_length if k == b'data' else n)
                   + ((16 if k == b'fmt ' else data_length if k == b'data' else n) & 1) for k, _, n in chunks)
    if total-8 > 0xffffffff:raise ValueError('Converted WAVE exceeds RIFF size')
    with Path(source).open('rb') as src, Path(target).open('wb') as dst:
        dst.write(b'RIFF'+struct.pack('<I', total-8)+b'WAVE')
        for kind, offset, length in chunks:
            if kind == b'fmt ':
                channels, rate = info['channels'], info['rate']
                fmt = struct.pack('<HHIIHH', 1, channels, rate, rate*channels*3, channels*3, 24)
                dst.write(b'fmt '+struct.pack('<I', 16)+fmt)
            elif kind == b'data':
                dst.write(b'data'+struct.pack('<I', data_length))
                for samples in _blocks(src, info):
                    values = np.rint(samples*gain*SCALE).clip(-SCALE, SCALE-1).astype('<i4')
                    dst.write(values.view(np.uint8).reshape(-1,4)[:,:3].tobytes())
                if data_length & 1:dst.write(b'\0')
            else:
                src.seek(offset-8); remaining = 8+length+(length & 1)
                while remaining:
                    raw = src.read(min(1024*1024, remaining))
                    if not raw:raise ValueError('Truncated WAVE metadata')
                    dst.write(raw); remaining -= len(raw)
        dst.flush(); os.fsync(dst.fileno())
    header = read_media_header(target)
    if (header.bits, header.channels, header.sample_rate, header.duration) != (
            24, info['channels'], info['rate'], Fraction(info['frames'], info['rate'])):
        raise ValueError('Converted WAVE timing or framing differs')
    # Independently read back every packed sample, not just the output header.
    import soundfile as sf
    max_error = 0.0
    with Path(source).open('rb') as src, sf.SoundFile(target) as decoded:
        if len(decoded) != info['frames']:raise ValueError('Converted WAVE frame count differs')
        for samples in _blocks(src, info):
            actual = decoded.read(len(samples)//info['channels'], dtype='float64', always_2d=True).reshape(-1)
            expected = np.clip(samples*gain,-1,1) if clip_overs else samples*gain
            max_error = max(max_error, float(np.max(np.abs(actual-expected))))
    if max_error > 1/SCALE:raise ValueError('Converted WAVE exceeds 24-bit rounding error')
    return dict(frames=info['frames'], sample_rate=info['rate'], channels=info['channels'],
                peak=peak, gain=gain, gain_db=20*math.log10(gain), max_error=max_error,
                clipped_samples=overrange_samples if clip_overs and gain == 1 else 0)


def _fault(phase):pass


def convert(path, root, operation_id, track_id, *, expected_sha256=None, reduce_gain=False,
            clip_overs=False, guard=lambda:None):
    """Recover exact before/after states; never overwrite an unrelated occupant."""
    path, root = Path(path), Path(root)
    if str(UUID(operation_id)) != operation_id or path != path.resolve(strict=True):
        raise ValueError('Canonical conversion identity required')
    directory = root/operation_id/result_key(path)
    if root.resolve() != root or path.is_relative_to(root) or root.is_relative_to(path):
        raise ValueError('Conversion recovery must be outside the music directory')
    receipt_path = directory/'receipt.json'; original = directory/'original.wav'
    if not receipt_path.exists() and not receipt_path.is_symlink() and layout(path) is None:return None
    if path.is_relative_to('/Volumes'):raise ValueError('Device originals cannot be converted')
    _regular(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.resolve() != directory:raise ValueError('Conversion recovery redirects')
    if receipt_path.exists() or receipt_path.is_symlink():
        _regular(receipt_path); receipt = json.loads(receipt_path.read_bytes())
        validate_receipt(receipt)
        if (receipt.get('format'),receipt.get('path'),receipt.get('operation_id'),receipt.get('track_id')) != (
                FORMAT,str(path),operation_id,track_id):raise ValueError('Conversion receipt identity differs')
        if expected_sha256 and expected_sha256 not in (receipt['before_sha256'],receipt['after_sha256']):
            raise ValueError('Conversion does not match the saved audio digest')
        _regular(original)
        if _sha(original) != receipt['before_sha256']:raise ValueError('Original WAVE recovery bytes changed')
        current, current_epoch = _current_digest(path)
        if current == receipt['after_sha256']:
            receipt['epoch'] = current_epoch; guard(); return receipt
        if current != receipt['before_sha256']:raise ValueError('WAVE changed during conversion recovery; original retained')
        stage = Path(receipt['stage'])
        if stage.parent != path.parent or not stage.name.startswith('.soundbox-wave-'):
            raise ValueError('Conversion stage identity differs')
        _regular(stage)
        if _sha(stage) != receipt['after_sha256']:raise ValueError('Converted recovery bytes changed')
        before = identity(path.stat()); guard()
        if identity(path.stat()) != before:raise ValueError('WAVE changed before replacement')
        os.replace(stage,path); sync_dir(path.parent); _fault('replaced')
        receipt['epoch'] = identity(path.stat()); atomic_bytes(receipt_path,json.dumps(receipt).encode())
        return receipt
    guard()
    with acquire_source(SourceContract(path,sha256=expected_sha256), directory) as snapshot:
        if layout(snapshot.path) is None:raise ValueError('Float WAVE changed during acquisition')
        if original.exists() or original.is_symlink():
            _regular(original)
            if _sha(original) != snapshot.sha256:raise ValueError('Original WAVE recovery collision')
        else:
            fd, backup_name = tempfile.mkstemp(prefix='.original-',dir=directory)
            try:
                with os.fdopen(fd,'wb') as dst, snapshot.path.open('rb') as src:
                    shutil.copyfileobj(src,dst,1024*1024); dst.flush(); os.fsync(dst.fileno())
                if original.exists() or original.is_symlink():raise ValueError('Original WAVE backup appeared during capture')
                os.replace(backup_name,original); sync_dir(directory)
            finally:Path(backup_name).unlink(missing_ok=True)
        if _sha(original) != snapshot.sha256:raise ValueError('Original WAVE backup verification failed')
        _fault('backup')
        fd, name = tempfile.mkstemp(prefix='.soundbox-wave-',suffix='.wav',dir=path.parent)
        os.close(fd); stage = Path(name)
        journaled = False
        try:
            measurements = render(snapshot.path,stage,reduce_gain=reduce_gain,clip_overs=clip_overs)
            source_stat = _regular(path)
            if identity(source_stat) != identity(snapshot.observation):raise ValueError('WAVE changed before conversion publication')
            os.chmod(stage,stat.S_IMODE(source_stat.st_mode))
            os.utime(stage,ns=(source_stat.st_atime_ns,source_stat.st_mtime_ns))
            with stage.open('rb') as verified:os.fsync(verified.fileno())
            sync_dir(path.parent)
            receipt = dict(format=FORMAT,operation_id=operation_id,track_id=track_id,path=str(path),
                before_sha256=snapshot.sha256, before_epoch=identity(snapshot.observation),
                after_sha256=_sha(stage),size=stage.stat().st_size,stage=str(stage),**measurements)
            atomic_bytes(receipt_path,json.dumps(receipt).encode()); journaled = True; _fault('prepared')
            guard()
            if _current_digest(stage)[0] != receipt['after_sha256']:raise ValueError('Converted WAVE changed before replacement')
            if identity(_regular(path)) != receipt['before_epoch']:raise ValueError('WAVE changed before replacement')
            os.replace(stage,path); sync_dir(path.parent); _fault('replaced')
            receipt['epoch'] = identity(path.stat()); atomic_bytes(receipt_path,json.dumps(receipt).encode())
            _fault('committed'); return receipt
        finally:
            if not journaled:stage.unlink(missing_ok=True)


def rebind(record, receipt):
    """Transfer musical edits only through an exact, verified sample-time mapping."""
    validate_receipt(receipt)
    if record is None:return None
    validate_result(record)
    if record['path'] != receipt['path'] or record['sha256'] not in (receipt['before_sha256'],receipt['after_sha256']):
        raise ValueError('Saved analysis belongs to different conversion bytes')
    if abs(record['duration']-receipt['frames']/receipt['sample_rate']) > 1e-9:
        raise ValueError('Saved analysis timing differs from the converted WAVE')
    result = deepcopy(record)
    result.setdefault('conversion_origin',dict(sha256=record['sha256'],epoch=record['epoch'],size=record['size']))
    result.update(sha256=receipt['after_sha256'],epoch=receipt['epoch'],size=receipt['size'])
    result['wave_conversion'] = {k:receipt[k] for k in (
        'format','before_sha256','after_sha256','gain','gain_db','max_error','clipped_samples')}
    for field in ('grid_draft','cue_draft'):
        if result.get(field) is not None:result[field]['base_sha256'] = result['sha256']
    validate_result(result)
    return result


def acknowledge(settings, imported, receipts):
    """Rebase the latest GUI edits, never a stale worker copy of musical data."""
    from . import prep_edits
    updated, records = deepcopy(settings), deepcopy(imported)
    saved = updated.setdefault(KEY, [])
    if not isinstance(saved, list):raise ValueError('Invalid saved WAVE conversion receipts')
    for receipt in receipts:
        validate_receipt(receipt)
        tid, path = receipt['track_id'], receipt['path']
        rec = records.get(tid)
        if not rec or rec.get('path') != path:raise ValueError('Imported WAVE identity changed before acknowledgement')
        if rec.get('size') not in (0,receipt['before_epoch'][2],receipt['size']):
            raise ValueError('Imported WAVE size changed before acknowledgement')
        rec['size'] = receipt['size']
        results = updated.get('analysis_results', {})
        key = result_key(path)
        if key in results:results[key] = rebind(results[key],receipt)
        pending = updated.get(prep_edits.KEY, [])
        prep_edits.validate(pending)
        for intent in pending:
            if intent['track_id'] != tid:continue
            if (intent['path'] != path or intent['native_ready'] or intent['prepared'] is not None
                    or intent['sha256'] and intent['sha256'] not in (receipt['before_sha256'],receipt['after_sha256'])
                    or not intent['sha256'] and intent['epoch'] != receipt['before_epoch']):
                raise ValueError('Pending prep identity differs from WAVE conversion')
            intent.update(sha256=receipt['after_sha256'],epoch=receipt['epoch'])
        prep_edits.validate(pending)
        prior = next((r for r in saved if (r.get('operation_id'),r.get('track_id')) == (receipt['operation_id'],tid)),None)
        if prior:
            if prior['after_sha256'] != receipt['after_sha256']:raise ValueError('Conflicting WAVE conversion receipt')
            saved.remove(prior)
        saved.append(deepcopy(receipt))
    return updated, records


def acknowledged(settings, job):
    saved=settings.get(KEY,[])
    if not isinstance(saved,list):raise ValueError('Invalid saved WAVE conversion receipts')
    result={}
    for receipt in saved:
        validate_receipt(receipt)
        if receipt['operation_id']!=job['id']:continue
        if (receipt['track_id'],receipt['path']) not in {(t['id'],t['path']) for t in job['tracks']}:
            raise ValueError('Saved WAVE conversion changed its import identity')
        if 'epoch' not in receipt:raise ValueError('WAVE conversion acknowledgement is incomplete')
        result[receipt['track_id']]=receipt['after_sha256']
    return result

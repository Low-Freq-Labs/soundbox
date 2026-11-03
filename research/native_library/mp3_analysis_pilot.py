"""One exact-plan CBR source pilot; separate approval and native acceptance.

Preparation renders and changes encrypted copies only. The new approval/journal
formats never provision a production authority. Transaction and recovery order
come from the existing analysis transport, with this pilot's own scope/guards.
"""
from dataclasses import asdict
from fractions import Fraction
import json
from pathlib import Path
import shutil
import tempfile

from soundbox import rekordbox_analysis_store as transport
from soundbox.analysis import validate_result
from soundbox.cdj_usb import run_renderer
from soundbox.native_media import MediaIdentity, epoch
from soundbox.native_registration_plan import _native_id, _uuid
from soundbox.rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from soundbox.rekordbox_grid import AnalysisImage
from soundbox.rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from soundbox.rekordbox_write import (ConflictError, RecoveryRequired, WriteProofError,
    _connect, _identity, _json, _regular, _state, assert_rekordbox_closed, encrypted_backup)
from soundbox.source_acquisition import SourceContract, acquire_source
from soundbox.state_io import atomic_bytes
from research.cdj_export.playlist_pilot import _apps_closed
from .registration_pilot import _paths
from . import mp3_analysis_plan as planner
from .mp3_seek import MP3Scan, scan_mp3, cbr_pvbr
from .mp3_timing import _renderer

FORMAT = 'soundbox-exact-cbr-analysis-pilot-v1'
PLAN_FORMAT = 'soundbox-exact-cbr-analysis-plan-v1'
APPROVAL_FORMAT = 'soundbox-exact-cbr-analysis-approval-v1'
REFERENCE_SHA256 = 'be7d00010d39bca7dd70e773f8b20f87e9aa2961ec2b05d6587e69c0d94c501c'
REFERENCE_NAME = 'SoundBox MP3 CBR candidate.mp3'
PAYLOADS = transport.PAYLOADS
NAMES = transport.NAMES
MAX_JSON_BYTES = 8 * 1024 * 1024


def _read_json(path):
    _regular(path)
    if not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise WriteProofError('CBR pilot JSON exceeds its byte budget')
    with path.open('rb') as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise WriteProofError('CBR pilot JSON exceeds its byte budget')
    value = json.loads(raw)
    if type(value) is not dict:
        raise WriteProofError('CBR pilot requires a JSON object')
    return value


def _plan_shape(record):
    fields = set(('format operation_id source source_file_id library_id schema_sha256 application_version '
        'data_root recovery_root share share_file_id audio scan track_id track_uuid result technical renderer '
        'seeds before after candidate source_write_authority payload_sha256').split())
    dictionaries = ('audio', 'scan', 'result', 'technical', 'renderer', 'seeds', 'before', 'after', 'candidate', 'payload_sha256')
    strings = fields - set(dictionaries) - {'source_file_id', 'share_file_id', 'source_write_authority'}
    if (set(record) != fields or any(type(record[key]) is not dict for key in dictionaries)
            or any(type(record[key]) is not str or not record[key] for key in strings)
            or any(type(record[key]) is not list or len(record[key]) != 2
                or any(type(value) is not int or value < 0 for value in record[key])
                for key in ('source_file_id', 'share_file_id'))):
        raise WriteProofError('Unknown CBR pilot plan fields/types')


def _closed():
    _apps_closed(); assert_rekordbox_closed()
    if _installed_version() != NATIVE_VERSION:
        raise WriteProofError('CBR pilot native application version changed')


def _scope(audio, result):
    audio.validate(); validate_result(result)
    expected = [[i % 4 + 1, 273 + 500 * i, 120.0] for i in range(64)]
    if (Path(audio.path).name != REFERENCE_NAME or audio.sha256 != REFERENCE_SHA256
            or result['path'] != audio.path or result['sha256'] != audio.sha256
            or result['epoch'] != list(audio.epoch) or result['size'] != audio.epoch[2]
            or result['bpm'] != 120.0 or result['key'] != '8B' or result['beats'] != expected
            or 'grid_draft' in result or result.get('first_cue') is not None or result.get('cue_draft')):
        raise WriteProofError('CBR pilot is restricted to the exact generated candidate and saved detector grid')


def _scan(record):
    if type(record) is not dict or set(record) != set(MP3Scan.__dataclass_fields__):
        raise WriteProofError('Unknown CBR pilot scan facts')
    return MP3Scan(**{key: tuple(value) if key in
        ('source_epoch', 'frame_offsets', 'frame_lengths', 'bitrates') else value
        for key, value in record.items()})


def _render_check(report, scan):
    _renderer(report)
    if (report['frames'], report['rate'], report['channels']) != (scan.sample_count, scan.sample_rate, scan.channels):
        raise WriteProofError('CBR pilot renderer differs from the scanned source endpoint')


def prepare(source, track_id, track_uuid, result, output, *, data_root, operation_id, renderer=None):
    """Create a reviewable copy-only plan; never create an approval file."""
    result = json.loads(json.dumps(result)); validate_result(result)
    _native_id(track_id); _uuid(track_uuid); _uuid(operation_id)
    source, output, data_root = (Path(path).expanduser().absolute() for path in (source, output, data_root))
    _paths(source, output, data_root); _regular(source); _closed()
    if Path(result['path']).name != REFERENCE_NAME or result['sha256'] != REFERENCE_SHA256:
        raise WriteProofError('Only the exact generated CBR pilot candidate may be prepared')
    source_id = [source.stat().st_dev, source.stat().st_ino]
    xml = source.with_name('masterPlaylists6.xml'); _regular(xml); xml_bytes = xml.read_bytes()
    share = source.parent / 'share'; transport._native_directory(share)
    output.mkdir(mode=0o700, parents=True)
    with tempfile.TemporaryDirectory(prefix='soundbox-cbr-pilot-') as area:
        scratch = Path(area).resolve(); before, after = scratch / 'before.db', scratch / 'after.db'
        with encrypted_backup(source) as copy:
            transport._copy_backup(copy, before)
        conn = _connect(before, readonly=True)
        try:
            library_id, version = _identity(conn); schema = _schema(conn); baseline = _state(conn)
            if version != '6000':
                raise WriteProofError('Unknown CBR pilot library version')
            base = transport._base(conn, track_id)
            if (base['track']['UUID'] != track_uuid or conn.execute(
                    'SELECT COUNT(*) FROM djmdContent WHERE UUID=?', (track_uuid,)).fetchone() != (1,)):
                raise ConflictError('CBR pilot native track/UUID changed')
        finally:
            conn.close()
        recovery = transport.shared_root(data_root, source, library_id)
        if (output.is_relative_to(recovery) or recovery.is_relative_to(output)
                or (recovery / 'transaction.json').exists() or (recovery / 'grid-transaction').exists()):
            raise RecoveryRequired('Resolve shared native recovery before preparing CBR analysis')
        with acquire_source(SourceContract(Path(result['path']), result['size'], REFERENCE_SHA256), scratch / 'audio') as captured:
            observed = scan_mp3(captured.path, expected_sha256=REFERENCE_SHA256)
            scan = MP3Scan(**{**asdict(observed), 'path': result['path'], 'source_epoch': epoch(captured.observation)})
            duration = Fraction(scan.sample_count, scan.sample_rate)
            audio = MediaIdentity(result['path'], scan.source_epoch, scan.sha256, int(duration),
                'mp3', 'mp3', duration.numerator, duration.denominator)
            _scope(audio, result)
            request = scratch / 'request.json'
            atomic_bytes(request, _json(dict(audio=str(captured.path), original_name=REFERENCE_NAME,
                beats=[dict(number=n, time_ms=ms, tempo=round(bpm * 100)) for n, ms, bpm in result['beats']])))
            rendered = (renderer or run_renderer)('saved-analysis', scratch, request)
            _render_check(rendered, scan)
            images = {}
            for name in NAMES:
                path = scratch / 'packet' / name; _regular(path)
                if not 0 < path.stat().st_size <= 64 * 1024 * 1024:
                    raise WriteProofError('CBR pilot rendered packet exceeds its byte budget')
                images[Path(name).suffix] = path.read_bytes()
            # The seek table is independently constructed from this captured
            # frame scan, never copied from a native reference or another song.
            images['.DAT'] = AnalysisImage.parse(images['.DAT']).replacing(b'PVBR', cbr_pvbr(scan))
        technical = dict(sample_rate=scan.sample_rate, channels=scan.channels, bits=16, bitrate=scan.bitrates[0])
        seeds = transport._seeds()
        candidate = planner.plan(base, audio, images, scan=scan, share_root=str(share), seeds=seeds,
                                 result=result, technical=technical)
        shutil.copyfile(before, after); conn = _connect(after)
        try:
            conn.execute('BEGIN IMMEDIATE'); transport._execute(conn, candidate['changes']); conn.commit()
            left = _connect(before, readonly=True)
            try:
                after_state = transport._verify(left, conn, candidate['changes'])
            finally:
                left.close()
        finally:
            conn.close()
        for path in (before, after):
            shutil.copyfile(path, output / path.name)
        record = dict(format=PLAN_FORMAT, operation_id=operation_id, source=str(source), source_file_id=source_id,
            library_id=library_id, schema_sha256=schema, application_version=NATIVE_VERSION,
            data_root=str(data_root), recovery_root=str(recovery), share=str(share),
            share_file_id=[share.stat().st_dev, share.stat().st_ino], audio=asdict(audio), scan=asdict(scan),
            track_id=track_id, track_uuid=track_uuid, result=result, technical=technical, renderer=rendered,
            seeds=seeds, before=baseline, after=after_state, candidate=candidate, source_write_authority=False)
    for name in ('before.xml', 'after.xml'):
        atomic_bytes(output / name, xml_bytes)
    for name in NAMES:
        atomic_bytes(output / name, images[Path(name).suffix])
    record['payload_sha256'] = {name: transport._sha(output / name) for name in PAYLOADS}
    atomic_bytes(output / 'plan.json', _json(record))
    audio.revalidate(); _closed()
    if [source.stat().st_dev, source.stat().st_ino] != source_id or xml.read_bytes() != xml_bytes:
        raise ConflictError('CBR pilot source changed during preparation')
    return load_plan(output)


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Private original CBR pilot plan required')
    record = _read_json(folder / 'plan.json')
    _plan_shape(record)
    if (record.get('format') != PLAN_FORMAT or record.get('application_version') != NATIVE_VERSION
            or record.get('source_write_authority') is not False):
        raise WriteProofError('Unknown CBR pilot plan/version')
    _uuid(record['operation_id']); _native_id(record['track_id']); _uuid(record['track_uuid'])
    _paths(Path(record['source']), folder, Path(record['data_root']))
    if (transport.shared_root(record['data_root'], record['source'], record['library_id']) != Path(record['recovery_root'])
            or Path(record['share']) != Path(record['source']).parent / 'share'
            or set(record['payload_sha256']) != set(PAYLOADS)):
        raise WriteProofError('CBR pilot share/recovery/payload scope changed')
    for name in PAYLOADS:
        _regular(folder / name)
        if transport._sha(folder / name) != record['payload_sha256'][name]:
            raise RecoveryRequired('CBR pilot payload changed')
    if (folder / 'before.xml').read_bytes() != (folder / 'after.xml').read_bytes():
        raise WriteProofError('CBR pilot cannot change playlist XML')
    audio = MediaIdentity.from_dict(record['audio']); scan = _scan(record['scan'])
    _scope(audio, record['result']); _render_check(record['renderer'], scan)
    with tempfile.TemporaryDirectory(prefix='soundbox-cbr-plan-check-') as area:
        scratch = Path(area).resolve()
        for name in ('before.db', 'after.db'):
            shutil.copyfile(folder / name, scratch / name)
        before, after = _connect(scratch / 'before.db', readonly=True), _connect(scratch / 'after.db', readonly=True)
        try:
            base = transport._base(before, record['track_id'])
            if (base['track']['UUID'] != record['track_uuid'] or before.execute(
                    'SELECT COUNT(*) FROM djmdContent WHERE UUID=?', (record['track_uuid'],)).fetchone() != (1,)):
                raise WriteProofError('CBR pilot native UUID changed')
            candidate = planner.plan(base, audio, {Path(name).suffix: (folder / name).read_bytes() for name in NAMES},
                scan=scan, share_root=record['share'], seeds=record['seeds'], result=record['result'], technical=record['technical'])
            if (_schema(before) != record['schema_sha256'] or _identity(before) != (record['library_id'], '6000')
                    or _state(before) != record['before'] or candidate != record['candidate']
                    or transport._verify(before, after, candidate['changes']) != record['after']):
                raise WriteProofError('CBR pilot no longer proves its exact complete delta')
        finally:
            before.close(); after.close()
    return record


def approval_fields(plan, plan_sha256):
    """Describe required approval fields; this function never creates approval."""
    return dict(format=APPROVAL_FORMAT, approved=True, plan_sha256=plan_sha256,
        source=plan['source'], library_id=plan['library_id'], schema_sha256=plan['schema_sha256'],
        application_version=plan['application_version'], operation_id=plan['operation_id'],
        track_id=plan['track_id'], track_uuid=plan['track_uuid'], source_sha256=plan['audio']['sha256'],
        data_root=plan['data_root'])


class _Lease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner; plan = owner.plan
        super().__init__(CueWriteAuthority(plan['source'], plan['library_id'], plan['schema_sha256'],
            'cbr-analysis-pilot:' + plan['track_id'], plan['application_version']), Path(plan['recovery_root']))

    def _guard(self):
        self.owner._approved(); _closed(); super()._guard(); self.owner._source_identity()

    def _record(self):
        return self.owner._record()


class MP3AnalysisPilot(transport.AnalysisStore):
    def __init__(self, folder, approval_file):
        self.folder = Path(folder).absolute(); self.plan = load_plan(self.folder)
        self.plan_sha = transport._sha(self.folder / 'plan.json')
        self.approval_file = Path(approval_file).absolute(); _read_json(self.approval_file)
        self.approval_sha = transport._sha(self.approval_file)
        self.database = Path(self.plan['source']); self.xml = self.database.with_name('masterPlaylists6.xml')
        self.root = Path(self.plan['recovery_root']); self.share = Path(self.plan['share'])
        self.journal = self.root / 'transaction.json'
        self.archive = self.root / ('cbr-analysis-pilot-' + self.plan['operation_id'])
        self._approved(); self.lease = _Lease(self)

    def _approved(self):
        plan, approval = _read_json(self.folder / 'plan.json'), _read_json(self.approval_file)
        if (self.folder != self.folder.resolve() or self.approval_file != self.approval_file.resolve()
                or transport._sha(self.folder / 'plan.json') != self.plan_sha
                or transport._sha(self.approval_file) != self.approval_sha
                or plan != self.plan or approval.get('approved') is not True
                or approval != approval_fields(self.plan, self.plan_sha)):
            raise WriteProofError('This exact CBR pilot plan/source/track has not been approved')

    def _record(self):
        _read_json(self.journal)
        return super()._record()

    def _payloads(self):
        for name in ('operation.json', 'result.json'):
            path = self.archive / name
            if path.exists() or path.is_symlink():
                _read_json(path)
        return super()._payloads()

    def _manifest(self):
        return {**super()._manifest(), 'format': FORMAT, 'application_version': self.plan['application_version'],
                'track_id': self.plan['track_id'], 'track_uuid': self.plan['track_uuid'],
                'source_sha256': self.plan['audio']['sha256']}

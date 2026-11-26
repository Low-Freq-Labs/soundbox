"""Separately accepted initial CBR MP3 analysis on the shared source lane.

Only bare, unlocked MPEG-1 CBR/no-Info stereo 44.1kHz registrations are admitted.
Grid/key/waveforms use captured bytes; gain/cues/audio/XML and other songs stay
unchanged. The legacy WAV authority, plan format and recovery remain independent.
"""
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, cast

from . import rekordbox_analysis_store as transport
from . import native_mp3_analysis_plan as planner
from .analysis import validate_result
from .cdj_usb import run_renderer
from .mp3_scan import MP3Scan, scan_mp3, cbr_pvbr, _validate_scan
from .native_media import MediaIdentity, epoch
from .native_registration_plan import _native_id, _uuid
from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from .rekordbox_grid import AnalysisImage
from .rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from .rekordbox_write import (ConflictError, RecoveryRequired, WriteProofError,
    _connect, _identity, _json, _regular, assert_rekordbox_closed, encrypted_backup)
from .source_acquisition import SourceContract, acquire_source
from .state_io import atomic_bytes, sync_dir
from .immutable_plan_proof import verify_once
from .native_transaction import hash_evidence

PROFILE = 'initial-mpeg1-cbr-no-info-stereo-44100-v1'
AUTHORITY_FORMAT = 'soundbox-cbr-analysis-authority-v1'
FORMAT = 'soundbox-native-cbr-analysis-v1'
PLAN_FORMAT = 'soundbox-native-cbr-analysis-plan-v1'
NAMES = transport.NAMES
PAYLOADS = transport.PAYLOADS
MAX_JSON_BYTES = 8 * 1024 * 1024


def _read_json_payload(path):
    path = Path(path); _regular(path)
    if not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise WriteProofError('CBR analysis JSON exceeds its byte budget')
    with path.open('rb') as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise WriteProofError('CBR analysis JSON exceeds its byte budget')
    value = json.loads(raw)
    if type(value) is not dict:
        raise WriteProofError('CBR analysis requires a JSON object')
    return raw, value


def _read_json(path):
    return _read_json_payload(path)[1]


@dataclass(frozen=True)
class CBRAnalysisAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted_reference_sha256: str
    accepted: bool
    application_version: str = NATIVE_VERSION

    @property
    def profile(self):
        return PROFILE

    def __post_init__(self):
        if self.accepted is not True:
            raise WriteProofError('CBR analysis requires separate native acceptance')
        CueWriteAuthority(self.database, self.library_id, self.schema_sha256, 'cbr-analysis', self.application_version)
        digest = self.accepted_reference_sha256
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise WriteProofError('Unknown CBR analysis acceptance')


def load_authority(path, source, library_id):
    try:
        body = _read_json(path)
        if body.pop('format', None) != AUTHORITY_FORMAT:
            return None
        authority = CBRAnalysisAuthority(**body)
        return authority if (authority.database, authority.library_id) == (str(source), library_id) else None
    except (OSError, ValueError, TypeError, KeyError, WriteProofError):
        return None


def _closed():
    assert_rekordbox_closed()
    if _installed_version() != NATIVE_VERSION:
        raise WriteProofError('CBR analysis application version changed')


def _scan(record):
    if type(record) is not dict or set(record) != set(MP3Scan.__dataclass_fields__):
        raise WriteProofError('Unknown CBR analysis scan facts')
    scan = MP3Scan(**cast(dict[str, Any], {key: tuple(value) if key in
        ('source_epoch', 'frame_offsets', 'frame_lengths', 'bitrates') else value
        for key, value in record.items()}))
    _validate_scan(scan)
    return scan


def _planner_result(result, audio):
    """Renew only a copied planner epoch after the exact SHA-pinned capture.

    The durable original record remains exact for job identity and newer drafts.
    Reconstructing this copy on load requires the same captured path/size/digest.
    """
    validate_result(result); audio.validate()
    if (result['path'] != audio.path or result['sha256'] != audio.sha256 or result['size'] != audio.epoch[2]
            or tuple(result['epoch'][:4]) != audio.epoch[:4]):
        raise WriteProofError('CBR analysis must retain the exact captured source path/bytes/identity')
    return {**result, 'epoch': list(audio.epoch)}


def _require_profile(authority, audio, technical, scan):
    _validate_scan(scan)
    if (not isinstance(authority, CBRAnalysisAuthority) or authority.profile != PROFILE
            or audio.family != 'mp3' or audio.codec != 'mp3' or scan.info_kind is not None or not scan.cbr
            or scan.sample_rate != 44100 or scan.channels != 2
            or type(technical) is not dict or set(technical) != {'sample_rate', 'channels', 'bits', 'bitrate'}
            or technical != dict(sample_rate=44100, channels=2, bits=16, bitrate=scan.bitrates[0])):
        raise WriteProofError('CBR authority accepts only MPEG-1 no-Info stereo CBR MP3 at44.1kHz')


def _render_check(report, scan):
    if (type(report) is not dict or report.get('source_write_authority') is not False
            or any(type(report.get(key)) is not int for key in ('frames', 'rate', 'channels', 'detail_columns'))
            or (report['frames'], report['rate'], report['channels'], report['detail_columns']) !=
               (scan.sample_count, scan.sample_rate, scan.channels, (scan.sample_count + 293) // 294)):
        raise WriteProofError('CBR analysis renderer differs from the independently scanned endpoint')


def prepare(authority, track_id, track_uuid, result, output, *, data_root, operation_id, renderer=None):
    """Persist an exact intent and prepare encrypted copies; no native SQL here."""
    if not isinstance(authority, CBRAnalysisAuthority):
        raise WriteProofError('Separate CBR analysis authority required')
    result = json.loads(json.dumps(result)); validate_result(result)
    _native_id(track_id); _uuid(track_uuid); _uuid(operation_id)
    source, output, data_root = Path(authority.database), Path(output).absolute(), Path(data_root).absolute()
    transport._paths(source, output, data_root); _regular(source); _closed()
    root = transport.shared_root(data_root, source, authority.library_id)
    if (root / 'transaction.json').exists() or (root / 'grid-transaction').exists():
        raise RecoveryRequired('Pending native edit')
    xml = source.with_name('masterPlaylists6.xml'); _regular(xml); xml_bytes = xml.read_bytes()
    share = source.parent / 'share'; transport._native_directory(share)
    source_id = [source.stat().st_dev, source.stat().st_ino]
    output.mkdir(mode=0o700, parents=True)
    atomic_bytes(output / 'intent.json', _json(dict(format=PLAN_FORMAT, operation_id=operation_id,
        track_id=track_id, track_uuid=track_uuid, result=result)))
    with tempfile.TemporaryDirectory(prefix='soundbox-cbr-analysis-') as area:
        scratch = Path(area).resolve(); before, after = scratch / 'before.db', scratch / 'after.db'
        with encrypted_backup(source) as copy:
            transport._copy_backup(copy, before)
        conn = _connect(before, readonly=True)
        try:
            if _identity(conn) != (authority.library_id, '6000') or _schema(conn) != authority.schema_sha256:
                raise ConflictError('CBR analysis library/schema changed')
            base = transport._base(conn, track_id)
            if (base['track']['UUID'] != track_uuid or conn.execute(
                    'SELECT COUNT(*) FROM djmdContent WHERE UUID=?', (track_uuid,)).fetchone() != (1,)):
                raise ConflictError('CBR analysis native identity changed')
        finally:
            conn.close()
        with acquire_source(SourceContract(Path(result['path']), result['size'], result['sha256']), scratch / 'audio') as captured:
            observed = scan_mp3(captured.path, expected_sha256=result['sha256'])
            scan = MP3Scan(**{**asdict(observed), 'path': result['path'], 'source_epoch': epoch(captured.observation)})
            duration = Fraction(scan.sample_count, scan.sample_rate)
            audio = MediaIdentity(result['path'], scan.source_epoch, scan.sha256, int(duration),
                'mp3', 'mp3', duration.numerator, duration.denominator)
            technical = dict(sample_rate=scan.sample_rate, channels=scan.channels, bits=16, bitrate=scan.bitrates[0])
            _require_profile(authority, audio, technical, scan)
            planned_result = _planner_result(result, audio)
            request = scratch / 'request.json'
            effective = transport.effective_result(planned_result)
            atomic_bytes(request, _json(dict(audio=str(captured.path), original_name=Path(audio.path).name,
                beats=[dict(number=n, time_ms=ms, tempo=round(bpm * 100)) for n, ms, bpm in effective['beats']])))
            rendered = (renderer or run_renderer)('saved-analysis', scratch, request)
            _render_check(rendered, scan)
            images = {}
            for name in NAMES:
                path = scratch / 'packet' / name; _regular(path)
                if not 0 < path.stat().st_size <= 64 * 1024 * 1024:
                    raise WriteProofError('CBR analysis packet exceeds its byte budget')
                images[Path(name).suffix] = path.read_bytes()
            images['.DAT'] = AnalysisImage.parse(images['.DAT']).replacing(b'PVBR', cbr_pvbr(scan))
        seeds = transport._seeds()
        candidate = planner.plan(base, audio, images, scan=scan, share_root=str(share), seeds=seeds,
            result=planned_result, technical=technical)
        shutil.copyfile(before, after); conn = _connect(after)
        try:
            conn.execute('BEGIN IMMEDIATE'); transport._execute(conn, candidate['changes']); conn.commit()
            left = _connect(before, readonly=True)
            try:
                baseline, after_state = transport._verify_pair(left, conn, candidate['changes'])
            finally:
                left.close()
        finally:
            conn.close()
        for path in (before, after):
            shutil.copyfile(path, output / path.name)
        record = dict(format=PLAN_FORMAT, operation_id=operation_id, source=str(source), source_file_id=source_id,
            library_id=authority.library_id, schema_sha256=authority.schema_sha256, application_version=authority.application_version,
            data_root=str(data_root), recovery_root=str(root), share=str(share), share_file_id=[share.stat().st_dev, share.stat().st_ino],
            audio=asdict(audio), scan=asdict(scan), track_id=track_id, track_uuid=track_uuid, result=result,
            technical=technical, renderer=rendered, seeds=seeds, authority=asdict(authority), before=baseline,
            after=after_state, candidate=candidate, source_write_authority=False)
    for name in ('before.xml', 'after.xml'):
        atomic_bytes(output / name, xml_bytes)
    for name in NAMES:
        atomic_bytes(output / name, images[Path(name).suffix])
    record['payload_sha256'] = dict(zip(PAYLOADS,
        hash_evidence((output/name for name in PAYLOADS),transport._sha),strict=True))
    atomic_bytes(output / 'plan.json', _json(record))
    audio.revalidate(); _closed()
    if [source.stat().st_dev, source.stat().st_ino] != source_id or xml.read_bytes() != xml_bytes:
        raise ConflictError('CBR analysis source changed during preparation')
    return load_plan(output)


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Private original CBR analysis plan required')
    raw_plan, record = _read_json_payload(folder / 'plan.json')
    if (record.get('format') != PLAN_FORMAT or record.get('application_version') != NATIVE_VERSION
            or record.get('source_write_authority') is not False):
        raise WriteProofError('Unknown CBR analysis plan/version')
    _uuid(record['operation_id']); _native_id(record['track_id']); _uuid(record['track_uuid'])
    transport._paths(Path(record['source']), folder, Path(record['data_root']))
    authority = CBRAnalysisAuthority(**record['authority'])
    if (authority.database, authority.library_id, authority.schema_sha256, authority.application_version) != (
            record['source'], record['library_id'], record['schema_sha256'], record['application_version']):
        raise WriteProofError('CBR plan exceeds its separate source authority')
    if (transport.shared_root(record['data_root'], record['source'], record['library_id']) != Path(record['recovery_root'])
            or Path(record['share']) != Path(record['source']).parent / 'share'
            or set(record['payload_sha256']) != set(PAYLOADS)):
        raise WriteProofError('CBR analysis share/recovery/payload scope changed')
    for name in PAYLOADS:
        _regular(folder / name)
    if hash_evidence((folder/name for name in PAYLOADS),transport._sha)!=[record['payload_sha256'][name] for name in PAYLOADS]:
        raise RecoveryRequired('CBR analysis payload changed')
    if (folder / 'before.xml').read_bytes() != (folder / 'after.xml').read_bytes():
        raise WriteProofError('CBR analysis cannot change playlist XML')
    audio = MediaIdentity.from_dict(record['audio']); scan = _scan(record['scan'])
    _require_profile(authority, audio, record['technical'], scan); _render_check(record['renderer'], scan)
    result = _planner_result(record['result'], audio)
    verify_once(folder, raw_plan, authority=_json(asdict(authority)), profile=PROFILE,
                verify=lambda: _verify_plan_reconstruction(folder, record, audio, scan, result))
    return record


def _verify_plan_reconstruction(folder, record, audio, scan, result):
    """Pure immutable paired-copy proof; no source/process/recovery guard reuse."""
    with tempfile.TemporaryDirectory(prefix='soundbox-cbr-plan-check-') as area:
        scratch = Path(area).resolve()
        for name in ('before.db', 'after.db'):
            shutil.copyfile(folder / name, scratch / name)
        before, after = _connect(scratch / 'before.db', readonly=True), _connect(scratch / 'after.db', readonly=True)
        try:
            base = transport._base(before, record['track_id'])
            if (base['track']['UUID'] != record['track_uuid'] or before.execute(
                    'SELECT COUNT(*) FROM djmdContent WHERE UUID=?', (record['track_uuid'],)).fetchone() != (1,)):
                raise WriteProofError('CBR analysis native UUID changed')
            candidate = planner.plan(base, audio, {Path(name).suffix: (folder / name).read_bytes() for name in NAMES},
                scan=scan, share_root=record['share'], seeds=record['seeds'], result=result, technical=record['technical'])
            if (_schema(before) != record['schema_sha256'] or _identity(before) != (record['library_id'], '6000')
                    or candidate != record['candidate']
                    or transport._verify(before, after, candidate['changes'], expected_before=record['before']) != record['after']):
                raise WriteProofError('CBR analysis no longer proves its exact complete delta')
        finally:
            before.close(); after.close()


class _Lease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner; plan = owner.plan
        super().__init__(CueWriteAuthority(plan['source'], plan['library_id'], plan['schema_sha256'],
            'cbr-analysis:' + plan['track_id'], plan['application_version']), Path(plan['recovery_root']))

    def _guard(self):
        self.owner._approved(); _closed(); super()._guard(); self.owner._source_identity()

    def _record(self):
        return self.owner._record()


class CBRAnalysisStore(transport.AnalysisStore):
    def __init__(self, authority, folder):
        if not isinstance(authority, CBRAnalysisAuthority):
            raise WriteProofError('Separate CBR analysis authority required')
        # These transport slots deliberately bind a separately accepted domain.
        self.authority = authority  # type: ignore[assignment]
        self.folder = Path(folder).absolute(); self.plan = load_plan(self.folder)
        self.plan_sha = transport._sha(self.folder / 'plan.json'); self.database = Path(self.plan['source'])
        self.xml = self.database.with_name('masterPlaylists6.xml'); self.root = Path(self.plan['recovery_root'])
        self.share = Path(self.plan['share']); self.journal = self.root / 'transaction.json'
        self.archive = self.root / ('cbr-analysis-' + self.plan['operation_id'])
        self._approved()
        self.lease = _Lease(self)  # type: ignore[assignment]

    def _approved(self):
        if (self.folder != self.folder.resolve() or _read_json(self.folder / 'plan.json') != self.plan
                or transport._sha(self.folder / 'plan.json') != self.plan_sha
                or self.plan['authority'] != asdict(self.authority)
                or (self.authority.database, self.authority.library_id, self.authority.schema_sha256) !=
                   (self.plan['source'], self.plan['library_id'], self.plan['schema_sha256'])):
            raise WriteProofError('CBR analysis plan/authority changed')
        _require_profile(self.authority, MediaIdentity.from_dict(self.plan['audio']), self.plan['technical'], _scan(self.plan['scan']))

    def _manifest(self):
        return {**super()._manifest(), 'format': FORMAT}

    def _record(self):
        _read_json(self.journal)
        return super()._record()

    def _payloads(self):
        for name in ('operation.json', 'result.json'):
            path = self.archive / name
            if path.exists() or path.is_symlink():
                _read_json(path)
        return super()._payloads()


def retire_confirmed(store, confirmed_operations):
    """Retain first/latest CBR full pairs and all permanent operation receipts."""
    if not isinstance(store, CBRAnalysisStore):
        raise WriteProofError('CBR retention requires its own accepted store')
    confirmed = set(confirmed_operations)
    with store.lease._lock():
        if store.journal.exists():
            raise RecoveryRequired('CBR retention cannot run during recovery')
        reference = store.root / 'cbr-analysis-reference.json'
        if not reference.exists():
            atomic_bytes(reference, _json(dict(format=FORMAT, operation_id=store.plan['operation_id'], source=str(store.database))))
        protected = _read_json(reference)
        if protected.get('format') != FORMAT or protected.get('source') != str(store.database):
            raise RecoveryRequired('Unknown CBR analysis reference retention')
        _uuid(protected['operation_id'])
        for archive in store.root.glob('cbr-analysis-*'):
            if not archive.is_dir() or archive.is_symlink():
                continue
            operation = archive.name.removeprefix('cbr-analysis-')
            if operation not in confirmed or operation == store.plan['operation_id']:
                continue
            _uuid(operation); transport._native_directory(archive)
            result = _read_json(archive / 'result.json')
            if (result.get('format') != FORMAT or result.get('source') != str(store.database)
                    or result.get('operation_id') != operation or result.get('outcome') != 'committed'):
                raise RecoveryRequired('Unknown confirmed CBR archive')
            plan_folder = Path(result['plan_folder'])
            if plan_folder.parent != store.folder.parent or plan_folder.name != operation or plan_folder != plan_folder.resolve():
                raise RecoveryRequired('CBR analysis retention redirects')
            choices = [(plan_folder, 'plan')]
            if operation != protected['operation_id']:
                choices.append((archive, 'archive'))
            for folder, kind in choices:
                marker = folder / 'retirement.json'
                payload = {name: result['payload_sha256'][name] for name in ('before.db', 'after.db', 'before.xml', 'after.xml')}
                receipt = dict(format=FORMAT, operation_id=operation, kind=kind, payload_sha256=payload)
                if marker.exists():
                    if _read_json(marker) != receipt:
                        raise RecoveryRequired('Unknown CBR retirement prefix')
                else:
                    for name, digest in payload.items():
                        if transport._sha(folder / name) != digest:
                            raise RecoveryRequired('CBR retention payload changed')
                    atomic_bytes(marker, _json(receipt))
                for name, digest in payload.items():
                    path = folder / name
                    if path.exists() or path.is_symlink():
                        if transport._sha(path) != digest:
                            raise RecoveryRequired('CBR retirement bytes changed')
                        store.lease._guard(); path.unlink(); sync_dir(folder)

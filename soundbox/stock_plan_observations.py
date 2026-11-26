"""Disposable negative source observations and bounded iPod planning metrics."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import Lock
import time

from .operation_control import checkpoint, record_metric
from .cache_io import atomic_cache_bytes


STOCK_SOURCE_PROFILE = 'ma446-mp3-pcm16-stereo-44100-v1'
_FORMAT = 'soundbox-stock-negative-sources-v1'
_MAX_BYTES = 8 * 1024 * 1024
_WAV_REASON = re.compile(r'(?:PCM_U8|PCM_16|PCM_24|PCM_32|FLOAT|DOUBLE) / [1-9][0-9]{0,5} Hz / '
                     r'[1-9][0-9]? ch WAV; validated stock boundary is PCM 16-bit / 44\.1 kHz / stereo')


def _known_negative(path: Path, reason: str) -> bool:
    if path.suffix.lower()=='.wav':return _WAV_REASON.fullmatch(reason) is not None
    if path.suffix.lower()!='.mp3':return False
    if reason in {'MPEG Layer 1; validated stock support is Layer III',
                  'MPEG Layer 2; validated stock support is Layer III'}:return True
    rate=re.fullmatch(r'([1-9][0-9]{0,5}) Hz MP3; validated stock rates are 32/44\.1/48 kHz',reason)
    if rate:return int(rate[1]) in {8000,11025,12000,16000,22050,24000}
    channels=re.fullmatch(r'([1-9][0-9]?)\-channel MP3; stock audio must be mono/stereo',reason)
    if channels:return 2<int(channels[1])<=32
    bitrate=re.fullmatch(r'([1-9][0-9]{0,5}) kbps MP3; stock limit is 8–320 kbps',reason)
    return bitrate is not None and (0<int(bitrate[1])<8 or int(bitrate[1])>320)


def source_epoch(observed: os.stat_result) -> tuple[int, ...]:
    return (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_nlink,
            observed.st_size, observed.st_mtime_ns, observed.st_ctime_ns)


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


class NegativeSourceOmissions:
    """Remember rejection only; never provide media admission or byte proofs."""
    def __init__(self, limit: int=4096, *, root: Path | None=None, profile: str='') -> None:
        self.limit=limit
        self.root=root
        self.profile=profile
        self.rows: OrderedDict[Path,tuple[tuple[int,...],str]]=OrderedDict()
        self.lock=Lock()
        self.dirty=False
        self._load()

    def _load(self) -> None:
        if self.root is None or not self.profile:return
        try:
            if self.root.resolve(strict=True)!=self.root:return
            path=self.root/'negative-stock-sources-v1.json'
            before=path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size>_MAX_BYTES:return
            with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as handle:
                if source_epoch(os.fstat(handle.fileno()))!=source_epoch(before):return
                raw=handle.read(_MAX_BYTES+1)
                if (source_epoch(os.fstat(handle.fileno()))!=source_epoch(before)
                        or source_epoch(path.lstat())!=source_epoch(before) or len(raw)!=before.st_size):return
            document=json.loads(raw)
            if (type(document)is not dict or set(document)!={'format','profile','rows','sha256'}
                    or document['format']!=_FORMAT or document['profile']!=self.profile):return
            rows=document['rows']
            if (type(rows)is not list or len(rows)>self.limit
                    or document['sha256']!=hashlib.sha256(_encoded(rows)).hexdigest()):return
            loaded: OrderedDict[Path,tuple[tuple[int,...],str]]=OrderedDict()
            for row in rows:
                if (type(row)is not list or len(row)!=3 or type(row[0])is not str
                        or len(row[0])>4096 or not Path(row[0]).is_absolute()
                        or type(row[1])is not list or len(row[1])!=7
                        or any(type(value)is not int for value in row[1])
                        or not stat.S_ISREG(row[1][2]) or row[1][3]<1 or row[1][4]<0
                        or type(row[2])is not str or not _known_negative(Path(row[0]),row[2])):return
                path=Path(row[0])
                if str(path)!=row[0] or path in loaded:return
                loaded[path]=(tuple(row[1]),row[2])
            self.rows=loaded
        except (OSError,ValueError,TypeError,OverflowError,RecursionError):
            # Disposable rejection facts are optional; corruption never admits media.
            return

    def flush(self) -> None:
        """Publish one bounded negative index per plan, never an audio snapshot."""
        if self.root is None or not self.profile:return
        with self.lock:
            if not self.dirty:return
            rows=[[str(path),list(epoch),reason] for path,(epoch,reason) in self.rows.items()]
            raw=_encoded({'format':_FORMAT,'profile':self.profile,'rows':rows,
                          'sha256':hashlib.sha256(_encoded(rows)).hexdigest()})
            if len(raw)>_MAX_BYTES:return
            try:
                if self.root.resolve()!=self.root:return
                self.root.mkdir(parents=True,exist_ok=True)
                if self.root.resolve(strict=True)!=self.root:return
                atomic_cache_bytes(self.root/'negative-stock-sources-v1.json',raw)
            except OSError:
                return
            self.dirty=False

    def get(self, path: Path, epoch: tuple[int,...]) -> str | None:
        with self.lock:
            row=self.rows.pop(path,None)
            if row is None:return None
            if row[0]!=epoch:
                self.dirty=True
                return None
            self.rows[path]=row
            return row[1]

    def current(self, path: Path) -> str | None:
        """Reobserve an exact canonical source; no media open or admission."""
        checkpoint()
        try:
            if path.resolve(strict=True)!=path:
                self.discard(path)
                return None
            before=path.lstat()
            if not stat.S_ISREG(before.st_mode):
                self.discard(path)
                return None
            reason=self.get(path,source_epoch(before))
            if reason and (source_epoch(path.lstat())!=source_epoch(before)
                           or path.resolve(strict=True)!=path):
                self.discard(path)
                return None
            checkpoint()
            if reason:record_metric('stock_negative_source_current_hits')
            return reason
        except OSError:
            self.discard(path)
            return None

    def remember(self, path: Path, epoch: tuple[int,...], reason: str) -> None:
        if not _known_negative(path,reason):return
        with self.lock:
            self.rows.pop(path,None)
            self.rows[path]=(epoch,reason)
            while len(self.rows)>self.limit:self.rows.popitem(last=False)
            self.dirty=True

    def discard(self, path: Path) -> None:
        with self.lock:
            if self.rows.pop(path,None) is not None:self.dirty=True


_STAGES={
    'stock':frozenset(('capture','sources','inventory','playlists','prune',
                       'metadata','artwork','render','finalize')),
    'dual':frozenset(('reconcile','source_setup','source_verification','stock',
                      'rockbox_paths','artwork_ownership','finalize')),
}


class PlanTimings:
    """Fixed phase names, integer microseconds, existing operation metric owner."""
    def __init__(self, kind: str) -> None:
        self.kind=kind
        self.started=self.previous=time.monotonic()

    def mark(self, stage: str) -> None:
        if stage not in _STAGES[self.kind]:raise ValueError('Unknown iPod planning phase')
        now=time.monotonic()
        record_metric(self.kind+'_plan_'+stage+'_us',max(0,round((now-self.previous)*1_000_000)))
        self.previous=now
        if stage=='finalize':
            record_metric(self.kind+'_plan_total_us',max(0,round((now-self.started)*1_000_000)))

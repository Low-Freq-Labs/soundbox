"""Captured technical identity for the accepted bare registration formats.

No source SQL, source authority, musical analysis or audio tagging lives here.
The original inode remains authoritative when a local snapshot is inspected.
"""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .media_probe import read_media_header
from .rekordbox_write import ConflictError,WriteProofError
from .source_acquisition import SourceContract,acquire_source

TYPES={('wav','pcm_s16le'):11,('wav','pcm_s24le'):11,
       ('aiff','pcm_s16be'):12,('aiff','pcm_s24be'):12,
       ('mp3','mp3'):1,('flac','flac'):5,('m4a','aac'):4,('m4a','alac'):6}


def epoch(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


@dataclass(frozen=True)
class MediaIdentity:
    path: str
    epoch: tuple[int,...]
    sha256: str
    seconds: int
    family: str
    codec: str
    duration_numerator: int
    duration_denominator: int
    wave_format_tag: int | None = None

    @property
    def file_type(self):return TYPES.get((self.family,self.codec))

    @classmethod
    def from_dict(cls,value):
        if set(value)!=set(cls.__dataclass_fields__):raise WriteProofError('Unknown captured media identity fields')
        result=cls(**{**value,'epoch':tuple(value['epoch'])});result.validate();return result

    def validate(self):
        path=Path(self.path)
        suffixes={'.wav':'wav','.aif':'aiff','.aiff':'aiff','.mp3':'mp3','.flac':'flac','.m4a':'m4a'}
        if (not path.is_absolute() or str(path)!=self.path or '..' in path.parts or '\0' in self.path
                or suffixes.get(path.suffix.lower())!=self.family or self.file_type is None
                or len(self.epoch)!=5 or any(type(n) is not int or n<0 for n in self.epoch)
                or self.epoch[1]<=0 or self.epoch[2]<=0
                or type(self.seconds) is not int or self.seconds<0
                or not isinstance(self.sha256,str) or len(self.sha256)!=64
                or any(c not in '0123456789abcdef' for c in self.sha256)
                or type(self.duration_numerator) is not int or type(self.duration_denominator) is not int
                or self.duration_numerator<=0 or self.duration_denominator<=0):
            raise WriteProofError('Unverified captured media facts')
        duration=Fraction(self.duration_numerator,self.duration_denominator)
        if duration>86400:raise WriteProofError('Media duration exceeds the registration budget')
        seconds=0 if self.family=='wav' and self.wave_format_tag==65534 else int(duration)
        if (self.seconds!=seconds or (self.family=='wav' and self.wave_format_tag not in (1,65534))
                or (self.family!='wav' and self.wave_format_tag is not None)):
            raise WriteProofError('Unverified native bare-duration framing')

    def revalidate(self):
        self.validate();path=Path(self.path)
        if path!=path.resolve(strict=True) or path.is_symlink() or epoch(path.stat())!=self.epoch:
            raise ConflictError('Captured media identity changed')


def capture_media(path,scratch,*,expected_sha256=None):
    path=Path(path).absolute()
    if path!=path.resolve(strict=True) or path.is_symlink():raise WriteProofError('Canonical media path required')
    with acquire_source(SourceContract(path,sha256=expected_sha256),Path(scratch)) as snapshot:
        header=read_media_header(snapshot.path);duration=header.duration;tag=header.wave_format_tag
        facts=MediaIdentity(str(path),epoch(snapshot.observation),snapshot.sha256,
            0 if tag==65534 else int(duration),header.family,header.codec,duration.numerator,duration.denominator,tag)
        facts.validate();return facts

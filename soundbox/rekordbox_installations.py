"""Bounded installed-app observations, never library or write acceptance."""
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib


@dataclass(frozen=True)
class RekordboxInstallation:
    bundle: Path
    version: str


def installed_rekordbox(*, application_roots=None) -> tuple[RekordboxInstallation, ...]:
    roots = application_roots if application_roots is not None else (
        Path('/Applications'), Path.home() / 'Applications')
    found = []
    for root in roots:
        root = Path(root)
        candidates = [root / 'rekordbox.app']
        try:
            # One directory level, only the vendor's named version containers.
            # A general application/filesystem walk is unnecessary here.
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= 4096:
                        return ()
                    if entry.name.startswith('rekordbox ') and entry.is_dir(follow_symlinks=False):
                        candidates.append(root / entry.name / 'rekordbox.app')
        except OSError:
            continue
        for bundle in candidates:
            info = bundle / 'Contents' / 'Info.plist'
            try:
                before = info.stat()
                if before.st_size > 1024 * 1024:
                    continue
                with info.open('rb') as stream:
                    raw = stream.read(1024 * 1024 + 1)
                after = info.stat()
                if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or len(raw) > 1024 * 1024):
                    continue
                data = plistlib.loads(raw)
                version = data.get('CFBundleShortVersionString')
                if type(version) is str and version and len(version) <= 128:
                    found.append(RekordboxInstallation(bundle, version))
            except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException):
                continue
    return tuple(found)


def installed_version() -> str:
    """A single observed version; mixed versions fail closed instead of guessing."""
    versions = {item.version for item in installed_rekordbox()}
    return next(iter(versions)) if len(versions) == 1 else ''

"""Explicit official-release checks; never download, execute or install code."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import plistlib
import re
import ssl
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .operation_control import checkpoint

REPOSITORY = 'https://github.com/Low-Freq-Labs/soundbox'
API = 'https://api.github.com/repos/Low-Freq-Labs/soundbox/releases/latest'
ASSET = 'Soundbox-macOS-arm64.pkg'
MAX_BYTES = 1024 * 1024
TIMEOUT = 10
_VERSION = r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})'
_TAG = re.compile(r'v' + _VERSION + r'-build\.([1-9][0-9]{0,9})\Z')


@dataclass(frozen=True)
class Build:
    version: str
    build: int
    profile: str


@dataclass(frozen=True)
class Release:
    version: str
    build: int
    page_url: str
    download_url: str


def _version(value):
    if type(value) is not str or re.fullmatch(_VERSION, value) is None:
        raise ValueError('Invalid Soundbox version')
    return tuple(int(part) for part in value.split('.'))


def current_build() -> Build:
    """Use the packaged running app's stamped identity, never a source revision."""
    if not getattr(sys, 'frozen', False):
        raise ValueError('Update checks are available in the installed app')
    path = Path(sys.executable).resolve().parents[1] / 'Info.plist'
    with path.open('rb') as stream:
        raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError('Invalid app build information')
    value = plistlib.loads(raw)
    version = value.get('CFBundleShortVersionString')
    build = value.get('CFBundleVersion')
    if (value.get('CFBundleIdentifier') != 'com.lowfreqlabs.soundbox'
            or type(build) is not str or re.fullmatch(r'[1-9][0-9]{0,9}', build) is None):
        raise ValueError('Invalid app build information')
    _version(version)
    return Build(version, int(build), value.get('SoundboxBuildProfile', ''))


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('Repeated release field')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError('Non-JSON release number')


def parse_release(raw: bytes, installed: Build) -> Release | None:
    """Only stable, exact official personal installers can be offered."""
    if installed.profile != 'personal':
        raise ValueError('This update channel is only available for personal builds')
    version = _version(installed.version)
    if type(installed.build) is not int or not 0 < installed.build < 10**10:
        raise ValueError('Invalid installed build')
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('Release response exceeds its safety limit')
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    if (not isinstance(value, dict) or value.get('draft') is not False
            or value.get('prerelease') is not False):
        raise ValueError('The release is not a published stable build')
    tag = value.get('tag_name')
    match = _TAG.fullmatch(tag) if type(tag) is str else None
    if match is None or value.get('html_url') != f'{REPOSITORY}/releases/tag/{tag}':
        raise ValueError('Unexpected release identity')
    assets = value.get('assets')
    if not isinstance(assets, list) or len(assets) > 100:
        raise ValueError('Invalid release assets')
    candidates = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == ASSET]
    if len(candidates) != 1:
        raise ValueError('The release has no unique macOS installer')
    asset = candidates[0]
    url = f'{REPOSITORY}/releases/download/{tag}/{ASSET}'
    if (asset.get('state') != 'uploaded' or asset.get('browser_download_url') != url
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= 2 * 1024**3):
        raise ValueError('Unexpected installer download')
    newer = (*map(int, match.groups()[:3]), int(match[4]))
    if newer <= (*version, installed.build):
        return None
    return Release('.'.join(match.groups()[:3]), int(match[4]), value['html_url'], url)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The update service redirected unexpectedly')


def latest_release(installed: Build) -> Release | None:
    """One bounded HTTPS metadata request, using the operating system trust store."""
    if installed.profile != 'personal':
        raise ValueError('This update channel is only available for personal builds')
    import truststore
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
    request = Request(API, headers={'Accept': 'application/vnd.github+json',
        'User-Agent': 'Soundbox-Update-Check', 'X-GitHub-Api-Version': '2022-11-28',
        'Accept-Encoding': 'identity'})
    checkpoint()
    deadline = time.monotonic() + TIMEOUT
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            if response.status != 200 or response.geturl() != API:
                raise ValueError('Unexpected update service response')
            if response.headers.get_content_type() != 'application/json':
                raise ValueError('Unexpected update service content')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                raise ValueError('Release response exceeds its safety limit')
            chunks = []
            size = 0
            while True:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise TimeoutError('The update check timed out')
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if time.monotonic() >= deadline:
                    raise TimeoutError('The update check timed out')
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Release response exceeds its safety limit')
            raw = b''.join(chunks)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise ValueError('The update service is unavailable; try again later') from exc
    return parse_release(raw, installed)

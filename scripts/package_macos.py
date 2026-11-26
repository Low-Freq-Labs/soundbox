#!/usr/bin/env python3
"""Build the app-only native macOS beta installer; never install or publish it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from scripts.build_composition import ROOT, inspect_artifact, source_fingerprint

ASSET_NAME = 'Soundbox-macOS-arm64.pkg'
PACKAGE_ID = 'com.lowfreqlabs.soundbox.installer'
BUNDLE_ID = 'com.lowfreqlabs.soundbox'
PREFLIGHT_SOURCE = ROOT / 'scripts/installer_preflight.c'
PREINSTALL = '''#!/bin/sh
set -eu
if [ "${3:-}" != "/" ]; then
    echo "Soundbox: Install on the running Mac's startup volume." >&2
    exit 1
fi
exec "$(/usr/bin/dirname "$0")/soundbox-installer-preflight"
'''
WELCOME = '''<!doctype html><html><body><h1>Soundbox beta</h1>
<p>For Apple Silicon Macs running macOS 14 or later.</p>
<p>Quit Soundbox normally before installing or updating. This installer replaces
only /Applications/Soundbox.app. Your library, playlists, artwork, settings,
credentials and original audio remain in their existing locations.</p>
<p>This beta is not notarized by Apple and has no Developer ID Installer signature.
The app uses a local signing certificate. macOS may require your manual approval
in System Settings &gt; Privacy &amp; Security. Only approve the official download
if you trust its source. The installer never disables macOS security.</p>
<p>When installation finishes, open Soundbox from Applications. First launch asks
which library to use and whether to clean titles/artists and fetch artwork.</p>
</body></html>'''
CONCLUSION = '''<!doctype html><html><body><h1>Soundbox is installed</h1>
<p>Open Soundbox from Applications after closing this installer.</p>
<p>Your existing Soundbox library and settings are preserved during an update.</p>
</body></html>'''


def run(args):
    return subprocess.run([str(value) for value in args], check=True, capture_output=True, text=True)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(app):
    """Complete staged payload witness, including intentional framework links."""
    result = {}
    for path in sorted(app.rglob('*')):
        value = path.lstat(); key = str(path.relative_to(app))
        if stat.S_ISREG(value.st_mode):
            result[key] = ['file', stat.S_IMODE(value.st_mode), value.st_size, digest(path)]
        elif stat.S_ISDIR(value.st_mode):
            result[key] = ['directory', stat.S_IMODE(value.st_mode)]
        elif stat.S_ISLNK(value.st_mode):
            if not path.resolve().is_relative_to(app.resolve()):
                raise ValueError('App contains an escaping or broken framework link')
            if not path.exists():
                raise ValueError('App contains a broken framework link')
            result[key] = ['link', os.readlink(path)]
        else:
            raise ValueError('App contains an unsupported filesystem entity')
    return result


def app_identity(app):
    if app.is_symlink() or not app.is_dir() or app.name != 'Soundbox.app':
        raise ValueError('Expected a real Soundbox.app bundle')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'Soundbox'
            or info.get('SoundboxBuildProfile') != 'personal'
            or info.get('SoundboxSourceDirty') is not False
            or not re.fullmatch(r'[0-9]+', info.get('CFBundleVersion', ''))
            or not re.fullmatch(r'[0-9a-f]{40}', info.get('SoundboxSourceRevision', ''))):
        raise ValueError('Unexpected app identity or build metadata')
    return info


def components():
    return [dict(RootRelativeBundlePath='Applications/Soundbox.app', BundleIsRelocatable=False,
                 BundleIsVersionChecked=True, BundleHasStrictIdentifier=True, BundleOverwriteAction='upgrade')]


def distribution(version):
    root = ET.Element('installer-gui-script', {'minSpecVersion': '2'})
    ET.SubElement(root, 'title').text = 'Soundbox'
    ET.SubElement(root, 'welcome', {'file': 'welcome.html', 'mime-type': 'text/html'})
    ET.SubElement(root, 'conclusion', {'file': 'conclusion.html', 'mime-type': 'text/html'})
    ET.SubElement(root, 'options', {'customize': 'never', 'require-scripts': 'false', 'hostArchitectures': 'arm64'})
    ET.SubElement(root, 'domains', {'enable_localSystem': 'true', 'enable_currentUserHome': 'false', 'enable_anywhere': 'false'})
    volume = ET.SubElement(root, 'volume-check', {'script': 'true'})
    allowed = ET.SubElement(volume, 'allowed-os-versions')
    ET.SubElement(allowed, 'os-version', {'min': '14.0'})
    outline = ET.SubElement(root, 'choices-outline')
    ET.SubElement(outline, 'line', {'choice': 'soundbox'})
    choice = ET.SubElement(root, 'choice', {'id': 'soundbox', 'visible': 'false', 'title': 'Soundbox'})
    ET.SubElement(choice, 'pkg-ref', {'id': PACKAGE_ID})
    ET.SubElement(root, 'pkg-ref', {'id': PACKAGE_ID, 'version': version, 'onConclusion': 'none'}).text = 'Soundbox-app.pkg'
    return ET.tostring(root, encoding='unicode', xml_declaration=True)


def build(app, output):
    app = Path(app).absolute(); output = Path(output).absolute()
    if output.name != ASSET_NAME or output.exists() or output.is_symlink():
        raise ValueError('Use a fresh output path named ' + ASSET_NAME)
    if output.parent.resolve().is_relative_to(app.resolve()):
        raise ValueError('Installer output must be separate from the input app')
    receipt_path = output.with_suffix('.json')
    if receipt_path.exists():
        raise ValueError('Installer receipt already exists')
    info = app_identity(app)
    inspected = inspect_artifact(app, 'personal')
    if inspected['source_sha256'] != source_fingerprint():
        raise ValueError('Input app fingerprint differs from the current source')
    original = manifest(app)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='soundbox-package-', dir=output.parent) as directory:
        work = Path(directory); payload = work / 'payload'; staged = payload / 'Applications/Soundbox.app'
        staged.parent.mkdir(parents=True)
        run(['/usr/bin/ditto', app, staged])
        if manifest(staged) != original or manifest(app) != original:
            raise ValueError('Input app changed while staging the installer')
        app_identity(staged)
        scripts = work / 'scripts'; scripts.mkdir()
        preinstall = scripts / 'preinstall'; preinstall.write_text(PREINSTALL); preinstall.chmod(0o755)
        checker = scripts / 'soundbox-installer-preflight'
        run(['/usr/bin/xcrun', 'clang', '-arch', 'arm64', '-mmacosx-version-min=14.0', '-Wall', '-Wextra', '-Werror',
             PREFLIGHT_SOURCE, '-framework', 'CoreFoundation', '-o', checker])
        component = work / 'components.plist'; component.write_bytes(plistlib.dumps(components()))
        run(['/usr/bin/pkgbuild', '--root', payload, '--identifier', PACKAGE_ID,
             '--version', info['CFBundleVersion'], '--install-location', '/', '--ownership', 'recommended',
             '--component-plist', component, '--scripts', scripts, work / 'Soundbox-app.pkg'])
        resources = work / 'resources'; resources.mkdir()
        (resources / 'welcome.html').write_text(WELCOME); (resources / 'conclusion.html').write_text(CONCLUSION)
        dist = work / 'Distribution.xml'; dist.write_text(distribution(info['CFBundleVersion']))
        candidate = work / ASSET_NAME
        run(['/usr/bin/productbuild', '--distribution', dist, '--resources', resources,
             '--package-path', work, candidate])
        expanded = work / 'expanded'
        run(['/usr/sbin/pkgutil', '--expand-full', candidate, expanded])
        payloads = list(expanded.glob('*.pkg/Payload'))
        if (len(payloads) != 1 or sorted(p.name for p in payloads[0].iterdir()) != ['Applications']
                or sorted(p.name for p in (payloads[0] / 'Applications').iterdir()) != ['Soundbox.app']
                or manifest(payloads[0] / 'Applications/Soundbox.app') != original):
            raise ValueError('Built installer payload differs from the one verified app')
        app_identity(payloads[0] / 'Applications/Soundbox.app')
        if manifest(app) != original:
            raise ValueError('Input app changed during packaging')
        if source_fingerprint() != inspected['source_sha256']:
            raise ValueError('Source changed during packaging')
        receipt = dict(format='soundbox-macos-installer-v1', asset=ASSET_NAME, package_id=PACKAGE_ID,
            package_sha256=digest(candidate), bytes=candidate.stat().st_size,
            source_revision=info['SoundboxSourceRevision'], source_sha256=inspected['source_sha256'],
            source_dirty=False,
            version=info['CFBundleShortVersionString'], build=info['CFBundleVersion'],
            app_executable_sha256=digest(app / 'Contents/MacOS/Soundbox'), profile='personal',
            application_signature='locally verified; self-signed beta, not Developer ID',
            installer_signature='unsigned beta; no Apple notarization', install_location='/Applications/Soundbox.app',
            payload_paths=['Applications/Soundbox.app'], recipient_data_included=False,
            preflight_sha256=digest(PREFLIGHT_SOURCE))
        # Hard-link publication is exclusive and stays on the staging volume.
        with candidate.open('rb') as stream:
            os.fsync(stream.fileno())
        owned = candidate.stat()
        os.link(candidate, output)
        try:
            with receipt_path.open('x') as stream:
                json.dump(receipt, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            try:
                current = output.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    output.unlink()
            except FileNotFoundError:
                pass
            raise
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ASSET_NAME)
    args = parser.parse_args()
    print(json.dumps(build(args.app, args.output), indent=2))


if __name__ == '__main__':
    main()

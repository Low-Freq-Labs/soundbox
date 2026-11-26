# Authoritative shared-core / optional-provider PyInstaller composition.
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

sys.path.insert(0, str(Path(SPECPATH)))
from scripts.build_composition import composition, collection_options, runtime_submodule, runtime_asset, write_manifest

profile = os.environ.get("SOUNDBOX_BUILD_PROFILE", "personal")
config = composition(profile)
bundle_icon = str(Path(SPECPATH) / "build" / profile / "assets" / "Soundbox.icns")
manifest = write_manifest(profile, Path(SPECPATH) / "build" / profile / "composition")
datas = [(str(manifest), "."), ("assets/soundbox-wordmark.svg", "assets"),
         ("assets/soundbox-original-sb-icon.svg", "assets"),
         ("soundbox/my_settings_fields.json", "soundbox"),
         ("native/cdj/vendor/rbxport/LICENSE", "licenses/rbxport"),
         ("native/cdj/vendor/rbxport/crates/rbl-deck/vendor/rubberband/COPYING", "licenses/rbxport")]
binaries = [("build/native/libsoundbox_deck.dylib", "native"),
            ("build/native/libsoundbox_process_guard.dylib", "native"),
            ("build/native/soundbox-ipod-identity", "native"),
            ("build/cdj/release/soundbox-cdj", "native")]
hiddenimports = []
for package in config["submodules"]:
    hiddenimports += collect_submodules(package, filter=runtime_submodule)
for package in config["collections"]:
    options = collection_options(package)
    data, binary, imports = collect_all(package, **options)
    datas += data
    binaries += binary
    hiddenimports += imports


a = Analysis(
    ['soundbox_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH) / 'soundbox' / 'packaged_worker_hook.py')],
    excludes=config["excludes"],
    noarchive=False,
    optimize=0,
)
# The QtGui hook includes an unused optional virtual-keyboard plugin. Cocoa
# owns text input; neither the app nor retained libraries link this module.
a.binaries = [entry for entry in a.binaries if runtime_asset(entry[0])]
a.datas = [entry for entry in a.datas if runtime_asset(entry[0])]
# Include notices from the exact distributions represented in this analysis.
# A fresh owned subdirectory avoids retaining notices from an older composition.
import tempfile
from scripts.build_notices import collect as collect_notices
notice_parent = Path(tempfile.mkdtemp(prefix='notices-', dir=Path(SPECPATH) / 'build' / profile))
notice_root = collect_notices([entry[0] for entry in a.pure], notice_parent / 'runtime')
a.datas += Tree(str(notice_root), prefix='licenses/runtime')
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Soundbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[bundle_icon],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Soundbox',
)
app = BUNDLE(
    coll,
    name='Soundbox.app',
    icon=bundle_icon,
    bundle_identifier='com.lowfreqlabs.soundbox',
)

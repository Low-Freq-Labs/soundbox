# Authoritative shared-core / optional-provider PyInstaller composition.
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

sys.path.insert(0, str(Path(SPECPATH)))
from scripts.build_composition import composition, write_manifest

profile = os.environ.get("SOUNDBOX_BUILD_PROFILE", "personal")
config = composition(profile)
manifest = write_manifest(profile, Path(SPECPATH) / "build" / profile / "composition")
datas = [(str(manifest), "."), ("assets/soundbox-wordmark.svg", "assets"),
         ("assets/soundbox-original-sb-icon.svg", "assets"),
         ("soundbox/my_settings_fields.json", "soundbox"),
         ("native/cdj/vendor/rbxport/LICENSE", "licenses/rbxport"),
         ("native/cdj/vendor/rbxport/crates/rbl-deck/vendor/rubberband/COPYING", "licenses/rbxport")]
binaries = [("build/native/libsoundbox_deck.dylib", "native"),
            ("build/cdj/release/soundbox-cdj", "native")]
hiddenimports = []
for package in config["submodules"]:
    hiddenimports += collect_submodules(package)
for package in config["collections"]:
    data, binary, imports = collect_all(package)
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
    runtime_hooks=[],
    excludes=config["excludes"],
    noarchive=False,
    optimize=0,
)
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
    icon=['assets/Soundbox.icns'],
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
    icon='assets/Soundbox.icns',
    bundle_identifier='com.lowfreqlabs.soundbox',
)

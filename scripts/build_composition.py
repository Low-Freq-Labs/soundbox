"""Single packaging authority for public/personal contents and inspection."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
from pathlib import Path
import plistlib
import sys
from types import CodeType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soundbox.providers import profile_path, read_manifest

COMMON_COLLECTIONS = ("pyrekordbox", "sqlcipher3", "PIL", "soundfile", "zstandard", "Crypto")
COMMON_SUBMODULES = ("sqlalchemy", "clickwheel.ipod", "PySide6.QtMultimedia")
PERSONAL_PACKAGES = ("soundbox_personal", "yt_dlp")
DEVELOPER_PACKAGES = (
    "tests", "research", "sqlalchemy.testing", "sqlalchemy.ext.mypy",
    "mypy", "ast_serialize",
    "Crypto.SelfTest",
)
UNUSED_DEPENDENCY_MODULES = (
    "pyrekordbox.__main__",  # Dependency installation/build CLI, never the desktop reader.
    "sqlalchemy.dialects.mssql", "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.oracle", "sqlalchemy.dialects.postgresql",
)
REQUIRED_RUNTIME_MODULES = (
    "soundbox.providers", "soundbox.artwork", "soundbox.mp3_scan",
    "soundbox.native_mp3_analysis_plan", "soundbox.rekordbox_cbr_analysis_store",
    "soundbox.immutable_plan_proof",
    "desktop.native_timing",
    "desktop.artwork_requests",
    "soundbox.mac_process",
    "soundbox.native_cipher",
    "soundbox.native_snapshot",
    "soundbox.ipod_usb_identity", "soundbox.stock_ipod_identity",
    "desktop.native_analysis", "desktop.tag_controller",
    "desktop.source_intent_admission", "desktop.tag_state", "desktop.tag_order",
    "desktop.tag_selection_view", "desktop.quit_saves", "desktop.stock_playlist_order",
    "soundbox.rekordbox_tag_order", "soundbox.stock_playlist_order", "soundbox.stock_plan_observations",
    "soundbox.rekordbox_tag_rename", "soundbox.rekordbox_playlist_move",
    "soundbox.rekordbox_playlist_create",
    "soundbox.playlist_creation_placement",
)


def source_fingerprint(root: Path = ROOT) -> str:
    files: list[Path] = []
    for directory in ("soundbox", "desktop", "soundbox_personal", "native", "profiles", "assets"):
        files.extend(p for p in (root / directory).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".c", ".cpp", ".mm", ".h", ".metal", ".json", ".svg", ".icns", ".rs", ".toml", ".lock", ".sql"))
    files.extend(root / name for name in (
        "soundbox_app.py", "Soundbox.spec", "build_app.sh", "build_native.sh", "build_cdj.sh",
        "native/CMakeLists.txt",
        "requirements-core.txt", "requirements-personal.txt", "pyproject.toml",
        "scripts/build_composition.py", "scripts/app_provenance.py", "scripts/check_cdj_vendor.py"))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def composition(profile: str) -> dict:
    manifest = read_manifest(profile_path(profile))
    personal = profile == "personal"
    return {
        "manifest": manifest,
        "collections": (*COMMON_COLLECTIONS, *(("yt_dlp",) if personal else ())),
        "submodules": (*COMMON_SUBMODULES, *(("soundbox_personal",) if personal else ())),
        "excludes": [*DEVELOPER_PACKAGES, *UNUSED_DEPENDENCY_MODULES,
                     *(() if personal else PERSONAL_PACKAGES)],
    }


def runtime_submodule(name: str) -> bool:
    """Keep development tools and unused dependency backends out of bundles."""
    return not any(name == package or name.startswith(package + ".")
                   for package in (*DEVELOPER_PACKAGES, *UNUSED_DEPENDENCY_MODULES))


def collection_options(package: str) -> dict:
    """Keep raw dependency data aligned with the compiled module policy."""
    if package == "Crypto":
        return dict(include_py_files=False, filter_submodules=runtime_submodule,
                    exclude_datas=["**/SelfTest/**"])
    if package == "pyrekordbox":
        return dict(filter_submodules=runtime_submodule, exclude_datas=["**/__main__.py"])
    return {}


def write_manifest(profile: str, directory: Path) -> Path:
    data = dict(composition(profile)["manifest"])
    data["source_sha256"] = source_fingerprint()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "soundbox-build.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def _same_code(actual: CodeType, expected: CodeType) -> bool:
    """Compare executable content and positions, independent of bundle paths."""
    fields = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
              "co_stacksize", "co_flags", "co_code", "co_names", "co_varnames",
              "co_freevars", "co_cellvars", "co_name", "co_qualname", "co_firstlineno",
              "co_linetable", "co_exceptiontable")
    if (any(getattr(actual, field) != getattr(expected, field) for field in fields)
            or len(actual.co_consts) != len(expected.co_consts)):
        return False
    for left, right in zip(actual.co_consts, expected.co_consts):
        if isinstance(left, CodeType) or isinstance(right, CodeType):
            if not isinstance(left, CodeType) or not isinstance(right, CodeType) or not _same_code(left, right):
                return False
        elif type(left) is not type(right) or left != right:
            return False
    return True


def verify_runtime_code(pyz, *, root: Path = ROOT) -> list[str]:
    """Require core capabilities and compare all bundled first-party Python code."""
    names = set(pyz.toc)
    leaked = sorted(name for name in names if any(
        name == package or name.startswith(package + ".") for package in DEVELOPER_PACKAGES))
    if leaked:
        raise ValueError("Developer Python modules leaked into PYZ: " + ", ".join(leaked))
    unused = sorted(name for name in names if any(
        name == package or name.startswith(package + ".") for package in UNUSED_DEPENDENCY_MODULES))
    if unused:
        raise ValueError("Unused dependency Python modules leaked into PYZ: " + ", ".join(unused))
    missing = sorted(set(REQUIRED_RUNTIME_MODULES) - names)
    if missing:
        raise ValueError("Shared runtime modules missing: " + ", ".join(missing))
    verified = []
    for name in sorted(names):
        if not any(name == prefix or name.startswith(prefix + ".")
                   for prefix in ("soundbox", "desktop", "soundbox_personal")):
            continue
        path = root / name.replace(".", "/")
        source = path.with_suffix(".py")
        if not source.is_file():
            source = path / "__init__.py"
        if not source.is_file():
            raise ValueError("Bundled first-party module has no source: " + name)
        actual = pyz.extract(name)
        expected = compile(source.read_bytes(), str(source), "exec", dont_inherit=True, optimize=0)
        if not isinstance(actual, CodeType) or not _same_code(actual, expected):
            raise ValueError("Bundled runtime code differs from tested source: " + name)
        verified.append(name)
    return verified


def verify_startup_scripts(archive, *, root: Path = ROOT) -> dict:
    """Check the actual bootloader script order and first-party script code."""
    scripts = [name for name, entry in archive.toc.items() if entry[-1] == 's']
    required = {'pyiboot01_bootstrap', 'packaged_worker_hook', 'pyi_rth_pyside6', 'soundbox_app'}
    missing = sorted(required - set(scripts))
    if missing:
        raise ValueError('Startup scripts missing: ' + ', '.join(missing))
    if (scripts[:2] != ['pyiboot01_bootstrap', 'packaged_worker_hook']
            or scripts[-1] != 'soundbox_app'):
        raise ValueError('Worker runtime hook must run first after bootstrap and before Qt and desktop entry')
    verified = []
    for name, relative in (
            ('packaged_worker_hook', 'soundbox/packaged_worker_hook.py'),
            ('soundbox_app', 'soundbox_app.py')):
        source = root / relative
        if not source.is_file():
            raise ValueError('Bundled startup script has no source: ' + name)
        try:
            actual = marshal.loads(archive.extract(name))
        except (EOFError, TypeError, ValueError) as exc:
            raise ValueError('Bundled startup script is invalid: ' + name) from exc
        expected = compile(source.read_bytes(), str(source), 'exec', dont_inherit=True, optimize=0)
        if not isinstance(actual, CodeType) or not _same_code(actual, expected):
            raise ValueError('Bundled startup code differs from tested source: ' + name)
        verified.append(name)
    return {'order': scripts, 'verified': verified}


def inspect_artifact(app: Path, profile: str) -> dict:
    """Inspect actual PYZ modules/files, rather than trusting hidden menus."""
    from PyInstaller.archive.readers import CArchiveReader

    resources = app / "Contents" / "Resources"
    manifest_path = resources / "soundbox-build.json"
    manifest = read_manifest(manifest_path)
    if not (resources / 'assets/soundbox-wordmark.svg').is_file():
        raise ValueError('Vector wordmark is missing from the bundle')
    if not (app / 'Contents/Frameworks/native/libsoundbox_process_guard.dylib').is_file():
        raise ValueError('Fresh native process guard is missing from the bundle')
    if not (app / 'Contents/Frameworks/native/soundbox-ipod-identity').is_file():
        raise ValueError('Bounded native iPod identity reader is missing from the bundle')
    expected = composition(profile)["manifest"]
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Artifact build manifest differs from selected profile")
    archive = CArchiveReader(str(app / "Contents" / "MacOS" / "Soundbox"))
    startup_scripts = verify_startup_scripts(archive)
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules = sorted(pyz.toc)
    verified_runtime = verify_runtime_code(pyz)
    present = {package: any(m == package or m.startswith(package + ".")
                            for m in modules) for package in PERSONAL_PACKAGES}
    if any(value != (profile == "personal") for value in present.values()):
        raise ValueError(f"Incorrect provider package contents: {present}")
    if "soundbox.providers" not in modules or "soundbox.artwork" not in modules:
        raise ValueError("Shared provider/storage core missing")
    forbidden = {"tests", "research", "mypy", "ast_serialize", "soundbox-ipod-ref", "soundbox-cdj-ref",
                 "library.json", ".sc_creds.json"}
    for path in app.rglob("*"):
        parts = path.relative_to(app).parts
        if forbidden.intersection(parts):
            raise ValueError(f"Developer/personal artifact leaked: {path}")
        if (path.name == '__main__.py' and path.parent.name == 'pyrekordbox'
                or any(parts[index:index + 3] == ('sqlalchemy', 'dialects', backend)
                       for backend in ('mssql', 'mysql', 'oracle', 'postgresql')
                       for index in range(len(parts) - 2))):
            raise ValueError(f"Unused dependency artifact leaked: {path}")
        if profile == "public" and any(
                part in PERSONAL_PACKAGES or part.startswith("yt_dlp-")
                for part in path.relative_to(app).parts):
            raise ValueError(f"Personal package leaked: {path}")
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("SoundboxBuildProfile") != profile:
        raise ValueError("Signed profile provenance missing/mismatched")
    if info.get("SoundboxSourceFingerprint") != manifest.get("source_sha256"):
        raise ValueError("Source fingerprint provenance mismatch")
    ids = [module["id"] for module in manifest["modules"]]
    if info.get("SoundboxModules") != ids:
        raise ValueError("Module provenance mismatch")
    return {"profile": profile, "modules": ids, "python_module_count": len(modules),
            "verified_runtime_modules": verified_runtime,
            "startup_scripts": startup_scripts,
            "personal_packages": present, "source_sha256": manifest["source_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--profile", required=True, choices=("public", "personal"))
    args = parser.parse_args()
    print(json.dumps(inspect_artifact(args.app, args.profile), indent=2))


if __name__ == "__main__":
    main()

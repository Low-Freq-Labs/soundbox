"""Single packaging authority for public/personal contents and inspection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soundbox.providers import profile_path, read_manifest

COMMON_COLLECTIONS = ("pyrekordbox", "sqlcipher3", "PIL", "soundfile", "zstandard")
COMMON_SUBMODULES = ("sqlalchemy", "clickwheel.ipod", "PySide6.QtMultimedia")
PERSONAL_PACKAGES = ("soundbox_personal", "yt_dlp")


def source_fingerprint(root: Path = ROOT) -> str:
    files = []
    for directory in ("soundbox", "desktop", "soundbox_personal", "native", "profiles", "assets"):
        files.extend(p for p in (root / directory).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".cpp", ".mm", ".h", ".metal", ".json", ".svg", ".rs", ".toml", ".lock", ".sql"))
    files.extend(root / name for name in (
        "soundbox_app.py", "Soundbox.spec", "build_app.sh", "build_native.sh", "build_cdj.sh",
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
        "excludes": [] if personal else list(PERSONAL_PACKAGES),
    }


def write_manifest(profile: str, directory: Path) -> Path:
    data = dict(composition(profile)["manifest"])
    data["source_sha256"] = source_fingerprint()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "soundbox-build.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def inspect_artifact(app: Path, profile: str) -> dict:
    """Inspect actual PYZ modules/files, rather than trusting hidden menus."""
    from PyInstaller.archive.readers import CArchiveReader

    resources = app / "Contents" / "Resources"
    manifest_path = resources / "soundbox-build.json"
    manifest = read_manifest(manifest_path)
    if not (resources / 'assets/soundbox-wordmark.svg').is_file():
        raise ValueError('Vector wordmark is missing from the bundle')
    expected = composition(profile)["manifest"]
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Artifact build manifest differs from selected profile")
    archive = CArchiveReader(str(app / "Contents" / "MacOS" / "Soundbox"))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules = sorted(pyz.toc)
    present = {package: any(m == package or m.startswith(package + ".")
                            for m in modules) for package in PERSONAL_PACKAGES}
    if any(value != (profile == "personal") for value in present.values()):
        raise ValueError(f"Incorrect provider package contents: {present}")
    if "soundbox.providers" not in modules or "soundbox.artwork" not in modules:
        raise ValueError("Shared provider/storage core missing")
    forbidden = {"tests", "research", "soundbox-ipod-ref", "soundbox-cdj-ref",
                 "library.json", ".sc_creds.json"}
    for path in app.rglob("*"):
        if forbidden.intersection(path.relative_to(app).parts):
            raise ValueError(f"Developer/personal artifact leaked: {path}")
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
            "personal_packages": present, "source_sha256": manifest["source_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--profile", required=True, choices=("public", "personal"))
    args = parser.parse_args()
    print(json.dumps(inspect_artifact(args.app, args.profile), indent=2))


if __name__ == "__main__":
    main()

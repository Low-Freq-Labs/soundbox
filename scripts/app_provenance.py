#!/usr/bin/env python3
"""Stamp or verify the source revision recorded in a Soundbox app bundle."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
import subprocess
import tomllib


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def source_metadata(repo: Path) -> dict[str, str | bool]:
    with (repo / "pyproject.toml").open("rb") as handle:
        version = str(tomllib.load(handle)["project"]["version"])
    return {
        "version": version,
        "revision": _git(repo, "rev-parse", "--verify", "HEAD"),
        "build": _git(repo, "rev-list", "--count", "HEAD"),
        "dirty": bool(_git(
            repo, "status", "--porcelain", "--untracked-files=normal")),
    }


def _info_path(app: Path) -> Path:
    return app / "Contents" / "Info.plist"


def stamp(app: Path, repo: Path) -> dict[str, str | bool]:
    """Write signed-bundle provenance after PyInstaller and before signing."""
    info_path = _info_path(app)
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    metadata = source_metadata(repo)
    info.update({
        "CFBundleShortVersionString": metadata["version"],
        "CFBundleVersion": metadata["build"],
        "SoundboxSourceRevision": metadata["revision"],
        "SoundboxSourceDirty": metadata["dirty"],
    })
    manifest_path = app / "Contents" / "Resources" / "soundbox-build.json"
    if manifest_path.exists():
        import json
        manifest = json.loads(manifest_path.read_text())
        info.update({
            "SoundboxBuildProfile": manifest["profile"],
            "SoundboxModules": [m["id"] for m in manifest["modules"]],
            "SoundboxSourceFingerprint": manifest["source_sha256"],
        })
    info_path.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    return metadata


def verify(app: Path, repo: Path) -> tuple[bool, str]:
    """Check bundle integrity and its declared revision against a clean checkout.

    This is signed build metadata, not independent proof that the compiler
    consumed that revision. It does not compare packaged code to source.
    """
    try:
        signature = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        return False, f"could not verify bundle signature: {exc}"
    if signature.returncode:
        return False, "bundle signature verification failed"
    with _info_path(app).open("rb") as handle:
        info = plistlib.load(handle)
    expected = source_metadata(repo)
    if expected["dirty"]:
        return False, "current checkout is dirty; it does not match committed Git source"
    revision = str(info.get("SoundboxSourceRevision") or "")
    dirty = info.get("SoundboxSourceDirty") is not False
    version = str(info.get("CFBundleShortVersionString") or "")
    if revision != expected["revision"]:
        return False, f"app revision {revision or 'missing'} != {expected['revision']}"
    if dirty:
        return False, "app was built from a dirty source tree"
    if version != expected["version"]:
        return False, f"app version {version or 'missing'} != {expected['version']}"
    if info.get("CFBundleVersion") != expected["build"]:
        return False, "app build number does not match current Git history"
    return True, (
        f"Signature valid; Soundbox {version} declares clean Git {revision}. "
        "Packaged source was not independently compared.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--stamp", action="store_true")
    parser.add_argument(
        "--repo", type=Path,
        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if args.stamp:
        metadata = stamp(args.app.resolve(), args.repo.resolve())
        state = "dirty" if metadata["dirty"] else "clean"
        print(
            f"Stamped Soundbox {metadata['version']} "
            f"({metadata['revision']}, {state}, build {metadata['build']})")
        return 0
    ok, message = verify(args.app.resolve(), args.repo.resolve())
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

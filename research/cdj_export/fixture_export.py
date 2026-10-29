"""Build, generate and compare an explicit local CDJ renderer fixture.

No device discovery, installed-library reads, app installation or USB writes.
The request is an explicit renderer DTO; live SoundBox selection is not wired.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from research.cdj_export.export_compare import compare, sha256


def local(workspace: Path, path: Path, *, exists=True) -> Path:
    canonical = path.resolve(strict=exists)
    if not canonical.is_relative_to(workspace) or canonical.is_relative_to("/Volumes"):
        raise ValueError("Path outside the explicit local workspace")
    return canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gate", choices=("native-equivalence", "strict-bytes"), required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    if workspace.is_relative_to("/Volumes") or workspace.parent == workspace:
        parser.error("Workspace must be an explicit local research directory")
    request = local(workspace, args.request)
    reference = local(workspace, args.reference)
    audio = local(workspace, args.audio)
    output = local(workspace, args.output, exists=False)
    if output.exists() or not output.parent.is_dir():
        parser.error("Output must be a new directory with an existing local parent")
    report_path = output.with_name(output.name + "-comparison.json")
    log_path = output.with_name(output.name + "-build.log")
    if report_path.exists() or log_path.exists():
        parser.error("Output report/log already exist; choose a new generation name")
    cargo = workspace / "toolchain-1.99/install/bin/cargo"
    rustc = workspace / "toolchain-1.99/install/bin/rustc"
    if not cargo.is_file() or not rustc.is_file():
        parser.error("Provision the documented isolated Rust toolchain first")
    manifest = Path(__file__).with_name("rbxport_adapter") / "Cargo.toml"
    environment = dict(os.environ, CARGO_HOME=str(workspace / "toolchain-1.99/cargo-home"),
                       CARGO_TARGET_DIR=str(workspace / "cargo-target"), RUSTC=str(rustc),
                       CARGO_BUILD_JOBS="2")
    with log_path.open("x") as log:
        subprocess.run([str(cargo), "build", "--locked", "--manifest-path", str(manifest)],
                       env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    binary = workspace / "cargo-target/debug/soundbox-cdj-fixture-export"
    # A failed build never falls through to an older binary.
    completed = subprocess.run([str(binary), str(workspace), str(request), str(output)],
                               check=True, capture_output=True, text=True)
    renderer = json.loads(completed.stdout)
    result = compare(workspace, reference, output, audio)
    result["gate"] = args.gate
    result["gate_passed"] = result["native_equivalence_match"] if args.gate == "native-equivalence" else result["strict_byte_match"]
    result["renderer"] = renderer
    provenance = {"request_sha256": sha256(request), "binary_sha256": sha256(binary),
                  "source_sha256": {name: hashlib.sha256((manifest.parent / name).read_bytes()).hexdigest()
                                    for name in ("Cargo.toml", "Cargo.lock", "src/main.rs")},
                  "shared_renderer_source_sha256": {name: sha256(manifest.parents[3] / "native/cdj" / name)
                                                     for name in ("Cargo.toml", "Cargo.lock", "src/lib.rs", "src/main.rs", "src/schema.json")},
                  "comparison_source_sha256": {name: sha256(Path(__file__).with_name(name))
                                                for name in ("export_compare.py", "fixture_export.py", "pdb_audit.py")},
                  "cargo": subprocess.check_output([str(cargo), "--version"], text=True).strip(),
                  "rustc": subprocess.check_output([str(rustc), "--version"], text=True).strip(),
                  "source_scope": "Explicit fixture DTO; application source adapter is validated separately",
                  "device_writes": False, "installed_app_changed": False}
    result["provenance"] = provenance
    report_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("gate", "gate_passed", "strict_byte_match",
                     "expected_files", "byte_identical_files", "all_database_rows_match", "extra_files")}, indent=2))
    if not result["gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

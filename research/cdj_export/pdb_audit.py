"""Read-only DeviceSQL structural report for copied research PDBs.

Independent of the historical prototype's incorrect big-endian row counts.
This diagnoses file structure; it does not certify CDJ compatibility.
"""

import argparse
import json
from pathlib import Path


from soundbox.cdj_pdb import audit


def read_copy(path: Path) -> dict:
    """Reject conventional macOS device paths and symlink inputs."""
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        # macOS /tmp is itself a symlink; use its canonical /private/tmp spelling.
        raise ValueError("Use a canonical copied-file path without symlinks")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to("/Volumes") or resolved.is_relative_to("/dev"):
        raise ValueError("Copy the PDB to a local research directory first")
    if path.suffix.lower() != ".pdb" or not path.is_file():
        raise ValueError("Expected a copied regular .pdb file")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Small-fixture audit limit is 64 MiB")
    return {"input": str(resolved), **audit(path.read_bytes())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdb", type=Path, nargs="+", help="Copied local PDBs to inspect")
    args = parser.parse_args()
    try:
        reports = [read_copy(path) for path in args.pdb]
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(reports, indent=2))
    if any(report["issues"] for report in reports):
        parser.exit(1)


if __name__ == "__main__":
    main()

# Developer tooling

Maintained build checks, fixture validation, isolated probes and explicit research entry points.

- `check_tests.py` / `verify_test_reports.py`: exact offscreen/Cocoa inventory and result gates.
- `check_fixtures.py` / `check_cdj_vendor.py`: immutable private fixtures and pinned vendor integrity.
- `check_layout.py`: short folder guides, local Markdown links, instruction parity and static imports.
- `build_composition.py` / `app_provenance.py`: package membership, source fingerprint and signed identity.
- `benchmark_startup.py`: disposable snapshot browsing; not live-library or physical-device acceptance.
- `prove_*`: scoped research tools; historical receipts do not authorize source publication.

Run from the root with `PYTHONPATH=. ./.venv/bin/python scripts/NAME.py`.
The full release entry point is `./check.sh --render`; never filter away its exit status.
Tools must isolate AppData, disable discovery and avoid live Keychain/source/device access by default.

See [release gate](../docs/test-release-gate.md) and [layout/retention](../docs/PROJECT_LAYOUT.md).

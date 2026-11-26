# Developer tooling

Maintained build checks, fixture validation, isolated probes and explicit research entry points.

- `check_tests.py` / `verify_test_reports.py`: isolated selected/area checks and exact full gates.
- `test_suites.py` / `test-suites.json`: static named areas, cumulative levels and platform plans.
- `update_test_costs.py`: scheduling hints from complete passing timing reports.
- `check_fixtures.py` / `check_cdj_vendor.py`: immutable private fixtures and pinned vendor integrity.
- `check_layout.py`: short folder guides, local Markdown links, instruction parity and static imports.
- `build_assets.py` / `build_notices.py`: default white icon and installed runtime dependency notices.
- `build_composition.py` / `app_provenance.py`: package membership, source fingerprint and signed identity.
- `package_macos.py` / `installer_preflight.c`: app-only native beta installer; [build and upgrade](../docs/macos-installer.md).
- `benchmark_startup.py`: disposable snapshot browsing; not live-library or physical-device acceptance.
- `prove_*`: scoped research tools; historical receipts do not authorize source publication.

Run from the root with `PYTHONPATH=. ./.venv/bin/python scripts/NAME.py`.
Use `./check.sh --list-suites`, then `--suite AREA... --level LEVEL`; `--plan` previews without Qt.
Exact `--focus SELECTOR... [--cocoa]` remains available; run relevant static checks too.
Before commit/push run `./check.sh --render`; never filter away its exit status.
Tools must isolate AppData, disable discovery and avoid live Keychain/source/device access by default.

See [release gate](../docs/test-release-gate.md) and [layout/retention](../docs/PROJECT_LAYOUT.md).

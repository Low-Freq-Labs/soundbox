# Fixture manifests

Tracked identities for private immutable offline regression inputs.

`waveform-reference.json` pins source/cache/images; `cdj-reference.json` pins the CDJ integration capture.
The large inputs live in ignored reference roots, never in app packages.

Run `PYTHONPATH=. ./.venv/bin/python scripts/check_fixtures.py` from the root.
Missing/mismatched input fails explicitly; never regenerate or rebaseline it during cleanup.
See [provisioning](../../docs/test-release-gate.md) and [retention](../../docs/PROJECT_LAYOUT.md).

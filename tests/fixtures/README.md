# Fixture manifests

Tracked identities for private immutable inputs and generated native references.

`waveform-reference.json` pins source/cache/images; `cdj-reference.json` pins the CDJ integration capture.
The large inputs live in ignored reference roots, never in app packages.
`native-metadata-retirement-v2.json` preserves the generated Content/model/Main-SQL
matrix with explicit Main-format UTC clock seeds. The earlier unsuffixed file
retains the rejected Agent 12-hour clock attempt for a refusal regression; it is
not a positive Main cleanup reference. Both byte hashes are pinned by the tests.
`native-metadata-album-cloud.json` pins24 generated album compositions and16
cloud update stages, with independent exact remote-USN SQL text casts. Its
component/static proof excludes Main execution and native source acceptance.

Run `PYTHONPATH=. ./.venv/bin/python scripts/check_fixtures.py` from the root.
Missing/mismatched input fails explicitly; never regenerate or rebaseline it during cleanup.
See [provisioning](../../docs/test-release-gate.md) and [retention](../../docs/PROJECT_LAYOUT.md).

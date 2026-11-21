# Tests

Behavioral, property, recovery, UI, native audio and Metal contracts.

- `test_*.py`: discovered suites; native deck cases run serially under Cocoa.
- `test_device_staging_filesystems.py`: real, private FAT32/exFAT image publication; never connected physical disks.
- [support](support/README.md): shared pure builders and native source loading; no tests at import time.
- [fixtures](fixtures/README.md): tracked manifests pinning protected offline references.
- [native](native/README.md): independently compiled native numerical oracles.

List areas with `./check.sh --list-suites`; combine `--suite AREA... --level quick|integration|recovery`.
Use `--plan` for selectors/platforms without loading tests; USB and iPod have separate suites.
Exact small fixes: `./check.sh --focus tests/test_name.py` plus affected integration/static checks.
Use `--cocoa` for selected macOS UI/native cases. Only selected modules load; Qt state stays private.
Before commit/push: `./check.sh --render`, all isolated offscreen workers then Cocoa/native.
The full gate runs every discovered case once; focused runs execute each selected case once.
Skips, expected failures and omitted selected cases fail; focused reports never count as full proof.
Tests must isolate AppData and suppress device/source discovery, live Keychain and network.
Never replace a fixture or weaken an oracle to make a refactor pass.

See [release provisioning](../docs/test-release-gate.md) and [test isolation traps](../docs/GOTCHAS.md).

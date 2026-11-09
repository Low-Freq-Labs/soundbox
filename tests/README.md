# Tests

Behavioral, property, recovery, UI, native audio and Metal contracts.

- `test_*.py`: discovered suites; native deck cases run serially under Cocoa.
- [support](support/README.md): shared pure builders and native source loading; no tests at import time.
- [fixtures](fixtures/README.md): tracked manifests pinning protected offline references.
- [native](native/README.md): independently compiled native numerical oracles.

Run `./check.sh --render` from the root: isolated offscreen workers, then required Cocoa/native checks.
Every discovered case must run exactly once; skips, expected failures and omitted cases fail.
Tests must isolate AppData and suppress device/source discovery, live Keychain and network.
Never replace a fixture or weaken an oracle to make a refactor pass.

See [release provisioning](../docs/test-release-gate.md) and [test isolation traps](../docs/GOTCHAS.md).

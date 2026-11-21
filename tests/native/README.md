# Native test oracles

Independent native helpers compiled by the Python regression suites.

These validate numerical/render contracts against production helpers without replacing the Cocoa gate.
Keep oracle calculations independent and preserve the pinned waveform/reference inputs.
No device/source discovery or live application state belongs here.

Run affected native suites locally; the complete root gate remains required before publication.
See [player invariants](../../docs/PLAYER.md) and [release gate](../../docs/test-release-gate.md).

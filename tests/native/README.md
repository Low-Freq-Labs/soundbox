# Native test oracles

Independent native helpers compiled by the Python regression suites.

[shader_math_runner.mm](shader_math_runner.mm) is compiled and driven by [test_waveform_gpu_math.py](../test_waveform_gpu_math.py).
[waveform_frame_runner.mm](waveform_frame_runner.mm) renders the production Metal pipeline at 1×/2× for [mode geometry](../test_waveform_mode_geometry.py).
Start in those Python suites for input construction and assertions.
Main 3BAND attributes stay independent of RGB; preview-producer and sampler checks live in `test_waveform_gpu_math.py`.

These validate numerical/render contracts against production helpers without replacing the Cocoa gate.
Keep oracle calculations independent and preserve the pinned waveform/reference inputs.
No device/source discovery or live application state belongs here.

Run affected native suites locally; the complete root gate remains required before publication.
See [player invariants](../../docs/PLAYER.md) and [release gate](../../docs/test-release-gate.md).

# Native packet renderer

Local Rust helper for explicit captured audio and disposable analysis proof outputs.

`Cargo.toml` pins the dependency; [src](src/README.md) owns the command implementation.
This renderer does not authorize source publication or accept a new codec/profile.
Keep private media and generated packets outside tracked source.

Use the root `./check.sh` for the integrated proof tests and locked helper builds.
Read [proof boundaries](../PROOFS.md) before invoking standalone tooling.

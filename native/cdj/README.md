# CDJ renderer

Rust helper for copied local catalogs and captured audio; it has no device/source publisher.

- [src](src/README.md): rendering, metadata completion, inspection, merges and analysis.
- `Cargo.toml` / `Cargo.lock`: locked build, using the existing isolated toolchain.
- `vendor/rbxport`: pinned upstream source and notices; never edit it for local style/docs.
- Python `soundbox.cdj_*` captures inputs and owns guarded device publication/recovery.

From the root: `./build_cdj.sh --test`; `scripts/check_cdj_vendor.py` checks pinned integrity.
Private captures and fixtures are excluded from packages. Hardware/distribution acceptance stays separate.

See [renderer details](RENDERER.md), [CDJ contract](../../docs/cdj-usb-export.md) and [handoff](../../docs/HANDOFF.md).

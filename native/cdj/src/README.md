# CDJ implementation

Production Rust helper modules, compiled into the standalone `soundbox-cdj` binary/library.

Start with `main.rs` for commands and `lib.rs` for shared renderer entry points.
Inspect neighboring modules for catalog merge/settings/analysis behavior before extending a command.
Inputs are explicit disposable local workspaces; Python owns source capture and USB publication.
Preserve unknown native fields and each catalog's unrelated baseline.

Run `./build_cdj.sh --test` from the repository root.
See [parent guide](../README.md) and [renderer contract](../RENDERER.md).

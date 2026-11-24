# CDJ implementation

Production Rust helper modules, compiled into the standalone `soundbox-cdj` binary/library.

- [main.rs](main.rs): command dispatch and inspection; [lib.rs](lib.rs): shared requests and metadata completion.
- [merge.rs](merge.rs) / [upgrade.rs](upgrade.rs): catalog reconciliation; [preferences.rs](preferences.rs): player settings.
- [analysis.rs](analysis.rs) / [saved_analysis.rs](saved_analysis.rs): captured-input analysis/packet helpers.
- [component_tempo.rs](component_tempo.rs) / [whole_tempo.rs](whole_tempo.rs): tempo policies shared with analysis.

Inputs are explicit disposable local workspaces; Python owns source capture and USB publication.
Preserve unknown native fields and each catalog's unrelated baseline.

Run `./build_cdj.sh --test` from the repository root.
See [parent guide](../README.md) and [renderer contract](../RENDERER.md).

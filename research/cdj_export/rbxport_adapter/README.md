# Fixture renderer adapter

Rust fixture entry point sharing the production CDJ library.

`Cargo.toml` binds the local library; [src](src/README.md) constructs explicit fixture requests.
No independent catalog writer, source discovery or device publication belongs here.
Private fixture inputs remain outside version control and packages.

From the root, `./build_cdj.sh --test` builds/tests this adapter with locked dependencies.
See [research boundaries](../README.md) and [production renderer](../../../native/cdj/README.md).

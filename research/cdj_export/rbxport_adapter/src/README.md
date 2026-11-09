# Fixture adapter source

`main.rs` maps explicit local fixture DTOs into the production CDJ renderer.

Keep metadata completion shared with production; do not duplicate a standalone writer.
Inputs and output workspaces must be explicit. This tool grants no USB/source authority.

Verify from the root with `./build_cdj.sh --test`.
See [adapter setup](../README.md) and [reference parity](../../EXPORT_PARITY.md).

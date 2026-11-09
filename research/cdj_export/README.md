# CDJ export research

Offline prototype/reference tools; production uses `native/cdj` and `soundbox.cdj_*`.

- [Prototype map](PROTOTYPE.md): codecs, comparisons, settings and playlist research.
- [Reference handoff](RBXPORT_HANDOFF.md) · [Export parity](EXPORT_PARITY.md)
- [rbxport_adapter](rbxport_adapter/README.md): local fixture adapter to the production library.

The old Python PDB candidate was rejected by hardware; shared read/write bugs are documented.
Research cannot provision native authority or write a device through production permissions.
Historical pilot approvals are not replay authorization. Private captures remain ignored.

Run the synthetic bridge/codec suites through the root `./check.sh`.
See [current CDJ contract](../../docs/cdj-usb-export.md) and [handoff](../../docs/HANDOFF.md).

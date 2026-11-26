#!/usr/bin/env bash
# Pinned local toolchain; no system Rust changes or runtime compilation.
set -euo pipefail
cd "$(dirname "$0")"
./.venv/bin/python scripts/check_cdj_vendor.py
CDJ_KIT="${SOUNDBOX_RUST_KIT:-$PWD/soundbox-cdj-ref/toolchain-1.99}"
if [ ! -x "$CDJ_KIT/install/bin/cargo" ]; then
  echo "Set SOUNDBOX_RUST_KIT to the pinned Rust kit described in docs/test-release-gate.md." >&2
  exit 2
fi
case "$("$CDJ_KIT/install/bin/rustc" --version)" in
  "rustc 1.99.0 "*) ;;
  *) echo "SoundBox requires the pinned Rust 1.99.0 toolchain." >&2; exit 2 ;;
esac
export CARGO_HOME="$CDJ_KIT/cargo-home"
export CARGO_TARGET_DIR="$PWD/build/cdj"
export RUSTC="$CDJ_KIT/install/bin/rustc"
export RUSTDOC="$CDJ_KIT/install/bin/rustdoc"
export CARGO_BUILD_JOBS=2
"$CDJ_KIT/install/bin/cargo" build --release --locked --manifest-path native/cdj/Cargo.toml

if [ "${1:-}" = "--test" ]; then
  "$CDJ_KIT/install/bin/cargo" test --release --locked --manifest-path native/cdj/Cargo.toml
  "$CDJ_KIT/install/bin/cargo" test --locked --manifest-path research/cdj_export/rbxport_adapter/Cargo.toml
fi

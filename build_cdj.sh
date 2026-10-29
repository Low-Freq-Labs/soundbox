#!/usr/bin/env bash
# Pinned local toolchain; no system Rust changes or runtime compilation.
set -euo pipefail
cd "$(dirname "$0")"
./.venv/bin/python scripts/check_cdj_vendor.py
CDJ_KIT="$PWD/soundbox-cdj-ref/toolchain-1.99"
export CARGO_HOME="$CDJ_KIT/cargo-home"
export CARGO_TARGET_DIR="$PWD/build/cdj"
export RUSTC="$CDJ_KIT/install/bin/rustc"
export CARGO_BUILD_JOBS=2
"$CDJ_KIT/install/bin/cargo" build --release --locked --manifest-path native/cdj/Cargo.toml

if [ "${1:-}" = "--test" ]; then
  "$CDJ_KIT/install/bin/cargo" test --release --locked --lib --manifest-path native/cdj/Cargo.toml
  "$CDJ_KIT/install/bin/cargo" test --locked --manifest-path research/cdj_export/rbxport_adapter/Cargo.toml
fi

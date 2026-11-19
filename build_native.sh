#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

cmake -S native -B build/native \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0
cmake --build build/native --config Release --parallel

echo "Built: native deck/process libraries and soundbox-ipod-identity"

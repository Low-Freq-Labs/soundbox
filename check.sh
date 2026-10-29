#!/usr/bin/env bash
# Release check: all tests in offscreen/Cocoa processes, required offline fixtures.
# Usage: ./check.sh [--render] [--native]
set -e
cd "$(dirname "$0")"
render=0
for option in "$@"; do
  case "$option" in
    --render) render=1 ;;
    --native) : ;; # compatibility: native is now always required
    *) echo "Usage: ./check.sh [--render] [--native]" >&2; exit 2 ;;
  esac
done
echo "== pyflakes =="
./.venv/bin/python -m pyflakes \
  soundbox soundbox_personal desktop tests scripts research soundbox_app.py
echo "OK"
echo "== native deck =="
./build_native.sh
echo "OK"
echo "== native CDJ renderer =="
./build_cdj.sh --test
echo "== mypy (engine) =="
./.venv/bin/python -m mypy soundbox/ soundbox_personal/
echo "== required offline fixtures =="
./.venv/bin/python scripts/check_fixtures.py
echo "== tests =="
# Buffer passing-test stdout/stderr and always publish the slowest cases. This
# keeps failure evidence useful while making runtime creep visible every run.
QT_QPA_PLATFORM=offscreen ./.venv/bin/python scripts/check_tests.py \
  --phase offscreen --report build/check/offscreen.json
if [ "$render" = 1 ]; then
  echo "== offscreen render =="
  PYTHONPATH=. ./.venv/bin/python -m desktop._shot /tmp/soundbox-check.png
fi
echo "== native Cocoa drawable/playback gate =="
QT_QPA_PLATFORM=cocoa ./.venv/bin/python scripts/check_tests.py \
  --phase native --report build/check/native.json
./.venv/bin/python scripts/verify_test_reports.py

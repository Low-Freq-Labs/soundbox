#!/usr/bin/env bash
# One-command health check: lint + full test suite (+ optional offscreen render).
# Usage: ./check.sh [--render] [--native]
set -e
cd "$(dirname "$0")"
render=0
native=0
for option in "$@"; do
  case "$option" in
    --render) render=1 ;;
    --native) native=1 ;;
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
echo "== mypy (engine) =="
./.venv/bin/python -m mypy soundbox/ soundbox_personal/
echo "== tests =="
# Buffer passing-test stdout/stderr and always publish the slowest cases. This
# keeps failure evidence useful while making runtime creep visible every run.
./.venv/bin/python -m unittest discover -s tests -b --durations 15
if [ "$render" = 1 ]; then
  echo "== offscreen render =="
  PYTHONPATH=. ./.venv/bin/python -m desktop._shot /tmp/soundbox-check.png
fi
if [ "$native" = 1 ]; then
  echo "== native Cocoa drawable/playback gate =="
  QT_QPA_PLATFORM=cocoa PYTHONPATH=. ./.venv/bin/python scripts/check_native.py
else
  echo "Native Cocoa drawable/playback gate NOT RUN (use --native); headless Metal math runs in the suite."
fi
echo "REQUESTED CHECKS PASSED (see reported skips and known expected failures)"

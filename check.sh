#!/usr/bin/env bash
# One-command health check: lint + full test suite (+ optional offscreen render).
# Usage: ./check.sh [--render]
set -e
cd "$(dirname "$0")"
echo "== pyflakes =="
./.venv/bin/python -m pyflakes \
  soundbox desktop tests scripts research soundbox_app.py
echo "OK"
echo "== native deck =="
./build_native.sh
echo "OK"
echo "== mypy (engine) =="
./.venv/bin/python -m mypy soundbox/
echo "== tests =="
# Buffer passing-test stdout/stderr and always publish the slowest cases. This
# keeps failure evidence useful while making runtime creep visible every run.
./.venv/bin/python -m unittest discover -s tests -b --durations 15
if [ "$1" = "--render" ]; then
  echo "== offscreen render =="
  PYTHONPATH=. ./.venv/bin/python -m desktop._shot /tmp/soundbox-check.png
fi
echo "ALL CHECKS PASSED"

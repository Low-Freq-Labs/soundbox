#!/usr/bin/env bash
# One-command health check: lint + full test suite (+ optional offscreen render).
# Usage: ./check.sh [--render]
set -e
cd "$(dirname "$0")"
echo "== pyflakes =="
./.venv/bin/python -m pyflakes soundbox/*.py desktop/*.py
echo "OK"
echo "== mypy (engine) =="
./.venv/bin/python -m mypy soundbox/
echo "== tests =="
./.venv/bin/python -m unittest discover -s tests
if [ "$1" = "--render" ]; then
  echo "== offscreen render =="
  PYTHONPATH=. ./.venv/bin/python -m desktop._shot /tmp/soundbox-check.png
fi
echo "ALL CHECKS PASSED"

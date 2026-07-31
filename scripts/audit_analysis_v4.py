#!/usr/bin/env python3
"""Compatibility entry point for the schema-neutral analysis audit."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_analysis import audit, main  # noqa: F401


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compatibility wrapper that delegates to ``scripts/check_summary.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_summary import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

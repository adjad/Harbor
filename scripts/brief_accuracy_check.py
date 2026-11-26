#!/usr/bin/env python3
"""How accurate is the Brief against this Mac's own Mail and Messages? Counts only.

Usage: python3 scripts/brief_accuracy_check.py [--no-write] [--out-dir DIR] [--window-days N]

Read-only on every source, no network, no model, no service or port. Prints a
counts table and writes ~/.moe/diagnostics/brief-accuracy-<timestamp>.json
(0600) holding counts, reason codes and per-run salted item hashes, never
content. Needs Full Disk Access for Mail and Messages; without it, it says so
and exits 3. Logic: service/proactive/accuracy_check.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.proactive.accuracy_check import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

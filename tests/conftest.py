"""Shared pytest setup.

The mykonos_meme_scout package is only declared installable from within
CORE/ (see CORE/pyproject.toml). Rather than requiring a pip install step
before tests can run, this conftest puts CORE/ on sys.path so
`import mykonos_meme_scout` works directly from a checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent / "CORE"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

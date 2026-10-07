"""Compatibility entry point for bounded read-only database inspection.

Age-only cleanup is disabled: snapshots are learning dependencies and the
old per-table commits could leave partial data loss. --days is accepted
only for CLI compatibility and has no filtering or retention effect.
Use scripts/db_audit.py directly for inspection. This is not compaction.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30,
                        help="Deprecated, ignored; no age selection or deletion")
    parser.add_argument("--execute", action="store_true",
                        help="Disabled: unsafe age-only cleanup is refused")
    args = parser.parse_args(argv)
    if args.execute:
        parser.error("Age-only cleanup is disabled: preserve learning dependencies first. No database opened.")
    print("Deprecated retention entry point: --days is ignored. Read-only JSON audit; "
          "no age selection, deletion or compaction.", file=sys.stderr)
    # Import after rejection; invoke the supervised CLI, not the cooperative API.
    script_directory = str(Path(__file__).resolve().parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    from db_audit import main as audit_main
    return audit_main([])


if __name__ == "__main__":
    raise SystemExit(main())

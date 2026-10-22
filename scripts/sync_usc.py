"""Synchronise the USC timetable widget into the catalogue.

Usage:
    python scripts/sync_usc.py                # live USC, synthetic per failed course
    python scripts/sync_usc.py --synthetic    # force invented fallback data
    python scripts/sync_usc.py --timeout 15   # shorter HTTP timeout

The raw result is stored in ``data/usc_sync.db`` (SQLite) and the app-facing
catalogue is rebuilt from the same legacy CSV pipeline used by the original
importer.  Restart the FastAPI server after a sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.usc_source import sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="No consultes la USC; genera datos inventados en la BBDD",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout HTTP en segundos")
    args = parser.parse_args()

    result = sync(force_synthetic=args.synthetic, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "\nListo. Reinicia uvicorn para que la API cargue el nuevo catálogo:\n"
        "  .\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""End-to-end refresh with a data-quality gate.

Usage:
    python -m card_analytics.pipeline --db sqlite:///cardrisk.db                    # rebuild star + reconcile
    python -m card_analytics.pipeline --db sqlite:///cardrisk.db --src data/raw/berka  # reload raw files first

Steps run in order: load (optional) -> star schema + model tests -> reconciliation.
If any step fails, the pipeline stops there, prints BLOCKED and exits 1, so nothing
downstream (dashboards, MI pack, models) is refreshed from bad data.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

from card_analytics import berka, reconcile, star
from card_analytics.load import DEFAULT_DB

Step = tuple[str, Callable[[], int]]


def run_steps(steps: list[Step]) -> int:
    for name, fn in steps:
        print(f"\n=== {name} ===")
        code = fn()
        if code != 0:
            print(f"\nBLOCKED: '{name}' failed, refresh stopped. Downstream outputs were not updated.")
            return 1
    print("\nAll checks passed. Refresh approved.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--src", type=Path, help="reload raw Berka files from this folder first")
    parser.add_argument("--report", type=Path, default=Path("docs/reconciliation-report.md"))
    args = parser.parse_args(argv)

    steps: list[Step] = []
    if args.src:
        steps.append(("load raw files", lambda: berka.main(["--src", str(args.src), "--db", args.db])))
    steps.append(("star schema + model tests", lambda: star.main(["--db", args.db])))
    steps.append(("reconciliation", lambda: reconcile.main(["--db", args.db, "--report", str(args.report)])))
    return run_steps(steps)


if __name__ == "__main__":
    sys.exit(main())

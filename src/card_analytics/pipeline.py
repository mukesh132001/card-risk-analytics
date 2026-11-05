"""End-to-end refresh with a data-quality gate.

Usage:
    python -m card_analytics.pipeline --db sqlite:///cardrisk.db                    # rebuild star + reconcile
    python -m card_analytics.pipeline --db sqlite:///cardrisk.db --src data/raw/berka  # reload raw files first

Steps run in order: load (optional) -> star schema + model tests -> reconciliation
-> PII-masked reporting layer -> customer segments -> default model -> data dictionary and lineage
-> Excel MI pack -> findings memo.
If any step fails, the pipeline stops there, prints BLOCKED and exits 1, so nothing
downstream (dashboards, MI pack, models) is refreshed from bad data.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

from card_analytics import berka, catalog, findings, mask, mipack, model, reconcile, segment, star
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
    parser.add_argument("--docs", type=Path, default=Path("docs"), help="where the dictionary and lineage go")
    parser.add_argument("--mi-pack", type=Path, default=mipack.DEFAULT_OUT, help="where the Excel MI pack goes")
    args = parser.parse_args(argv)

    steps: list[Step] = []
    if args.src:
        steps.append(("load raw files", lambda: berka.main(["--src", str(args.src), "--db", args.db])))
    steps.append(("star schema + model tests", lambda: star.main(["--db", args.db])))
    steps.append(("reconciliation", lambda: reconcile.main(["--db", args.db, "--report", str(args.report)])))
    steps.append(("reporting layer (PII masked)", lambda: mask.main(["--db", args.db])))
    steps.append(("customer segments", lambda: segment.main(["--db", args.db, "--docs", str(args.docs)])))
    steps.append(("loan default model", lambda: model.main(["--db", args.db, "--docs", str(args.docs)])))
    steps.append(("data dictionary + lineage", lambda: catalog.main(["--db", args.db, "--docs", str(args.docs)])))
    steps.append(("Excel MI pack", lambda: mipack.main(["--db", args.db, "--out", str(args.mi_pack)])))
    steps.append(("findings memo", lambda: findings.main(["--db", args.db, "--docs", str(args.docs)])))
    return run_steps(steps)


if __name__ == "__main__":
    sys.exit(main())

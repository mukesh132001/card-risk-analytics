"""Run every query in a .sql file and print the results as tables.

Usage:
    python -m card_analytics.ask sql/business_questions.sql --db sqlite:///cardrisk.db
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect


def split_queries(text: str) -> list[tuple[str, str]]:
    out = []
    for chunk in text.split(";"):
        if "select" not in chunk.lower():
            continue
        titles = [line[3:].strip() for line in chunk.splitlines() if line.startswith("-- Q")]
        out.append((titles[0] if titles else "Query", chunk.strip()))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    args = parser.parse_args(argv)
    conn = connect(args.db)
    for title, sql in split_queries(args.file.read_text()):
        print(f"\n{title}")
        print(pd.read_sql(sql, conn).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

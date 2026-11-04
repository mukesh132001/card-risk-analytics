"""Load card-transaction CSVs into the database and run data-quality checks.

Usage:
    python -m card_analytics.load --csv data/sample/transactions.csv
    python -m card_analytics.load --csv data/raw/creditcard.csv --table raw_creditcard --no-checks

The database comes from --db or the DATABASE_URL environment variable, for example
    postgresql+psycopg://card:card@localhost:5432/cardrisk   (Docker Postgres)
    sqlite:///cardrisk.db                                     (no server needed)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from card_analytics.quality import reconcile, run_checks

DEFAULT_DB = "postgresql+psycopg://card:card@localhost:5432/cardrisk"


def connect(url: str):
    """Return a connection pandas can write to.

    SQLite uses the standard library; anything else goes through SQLAlchemy.
    """
    if url.startswith("sqlite:///"):
        return sqlite3.connect(url.removeprefix("sqlite:///"))
    from sqlalchemy import create_engine

    return create_engine(url)


def load_csv(csv_path: Path, conn, table: str, if_exists: str = "replace") -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.to_sql(table, conn, if_exists=if_exists, index=False, chunksize=5000)
    return df


def read_table(conn, table: str) -> pd.DataFrame:
    return pd.read_sql(f"select * from {table}", conn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--table", default="raw_transactions")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--append", action="store_true", help="append instead of replacing the table")
    parser.add_argument("--no-checks", action="store_true", help="skip checks (for datasets with another schema)")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    source = load_csv(args.csv, conn, args.table, "append" if args.append else "replace")
    print(f"Loaded {len(source):,} rows from {args.csv} into {args.table}")
    if args.no_checks:
        return 0

    loaded = read_table(conn, args.table)
    results = run_checks(loaded) + [reconcile(source, loaded)]
    print("\nData-quality report")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name:<24} {r.detail}")
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the Berka star schema from the SQL models in dbt/models/berka.

Usage:
    python -m card_analytics.star --db sqlite:///cardrisk.db

The .sql files are written dbt-style ({{ source() }} / {{ ref() }}), so the same files
move into dbt unchanged later. This runner renders them, builds each model as a table
in dependency order, adds a generated dim_date, and runs the model tests:
unique keys, foreign keys to every dimension, and a to-the-cent reconciliation.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect
from card_analytics.quality import CheckResult

MODELS_DIR = Path(__file__).resolve().parents[2] / "dbt" / "models" / "berka"
BUILD_ORDER = ["dim_district", "dim_client", "dim_account", "fact_transactions", "fact_loans"]

SOURCE_RE = re.compile(r"\{\{\s*source\(\s*'berka'\s*,\s*'(\w+)'\s*\)\s*\}\}")
REF_RE = re.compile(r"\{\{\s*ref\(\s*'(\w+)'\s*\)\s*\}\}")


def render(sql: str) -> str:
    sql = SOURCE_RE.sub(lambda m: f"berka_{m.group(1)}", sql)
    return REF_RE.sub(lambda m: m.group(1), sql)


def execute(conn, statement: str) -> None:
    if isinstance(conn, sqlite3.Connection):
        conn.execute(statement)
        conn.commit()
    else:
        with conn.begin() as c:
            c.exec_driver_sql(statement)


def query(conn, sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, conn)


def build_dim_date(conn) -> pd.DataFrame:
    bounds = query(conn, "select min(trans_date) as lo, max(trans_date) as hi from berka_trans")
    days = pd.date_range(pd.to_datetime(bounds.lo[0]).normalize(), pd.to_datetime(bounds.hi[0]).normalize(), freq="D")
    dim = pd.DataFrame(
        {
            "date_key": days.strftime("%Y%m%d").astype(int),
            "date": days.date.astype(str),
            "year": days.year,
            "quarter": days.quarter,
            "month": days.month,
            "month_name": days.strftime("%b"),
            "year_month": days.strftime("%Y-%m"),
            "day_of_week": days.strftime("%a"),
            "is_weekend": (days.dayofweek >= 5).astype(int),
        }
    )
    dim.to_sql("dim_date", conn, if_exists="replace", index=False)
    return dim


def build(conn, models_dir: Path = MODELS_DIR) -> dict[str, int]:
    counts = {"dim_date": len(build_dim_date(conn))}
    for name in BUILD_ORDER:
        sql = render((models_dir / f"{name}.sql").read_text())
        execute(conn, f"drop table if exists {name}")
        execute(conn, f"create table {name} as {sql}")
        counts[name] = int(query(conn, f"select count(*) as n from {name}").n[0])
    return counts


def scalar(conn, sql: str):
    return query(conn, sql).iloc[0, 0]


def test_models(conn) -> list[CheckResult]:
    results = []
    keys = {
        "dim_date": "date_key", "dim_district": "district_id", "dim_client": "client_id",
        "dim_account": "account_id", "fact_transactions": "trans_id", "fact_loans": "loan_id",
    }
    for table, key in keys.items():
        dupes = int(scalar(conn, f"select count(*) - count(distinct {key}) from {table}"))
        nulls = int(scalar(conn, f"select count(*) from {table} where {key} is null"))
        results.append(CheckResult(f"{table}: unique {key}", dupes == 0 and nulls == 0, f"duplicates={dupes} nulls={nulls}"))

    relationships = [
        ("fact_transactions", "account_id", "dim_account", "account_id"),
        ("fact_transactions", "client_id", "dim_client", "client_id"),
        ("fact_transactions", "district_id", "dim_district", "district_id"),
        ("fact_transactions", "date_key", "dim_date", "date_key"),
        ("fact_loans", "account_id", "dim_account", "account_id"),
        ("fact_loans", "client_id", "dim_client", "client_id"),
        ("dim_account", "district_id", "dim_district", "district_id"),
        ("dim_account", "owner_client_id", "dim_client", "client_id"),
    ]
    for child, col, parent, pcol in relationships:
        orphans = int(scalar(conn, f"select count(*) from {child} c left join {parent} p on p.{pcol} = c.{col} where p.{pcol} is null"))
        results.append(CheckResult(f"{child}.{col} -> {parent}", orphans == 0, f"orphans={orphans}"))

    src = query(conn, "select count(*) as n, round(sum(signed_amount_czk), 2) as total from berka_trans")
    fact = query(conn, "select count(*) as n, round(sum(signed_amount_czk), 2) as total from fact_transactions")
    variance = round(float(src.total[0]) - float(fact.total[0]), 2)
    results.append(
        CheckResult(
            "reconcile: transactions",
            int(src.n[0]) == int(fact.n[0]) and variance == 0,
            f"rows {int(src.n[0]):,} vs {int(fact.n[0]):,}; net CZK {float(src.total[0]):,.2f} vs {float(fact.total[0]):,.2f}; variance={variance:.2f}",
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    args = parser.parse_args(argv)

    conn = connect(args.db)
    counts = build(conn)
    print("Built star schema")
    for name, n in counts.items():
        print(f"  {name:<18} {n:>10,} rows")

    results = test_models(conn)
    print("\nModel tests")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name:<46} {r.detail}")
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

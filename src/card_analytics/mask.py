"""Build the PII-masked reporting layer (rpt_*) that dashboards and analysts use.

Usage:
    python -m card_analytics.mask --db sqlite:///cardrisk.db

Client and account IDs are replaced by keyed-hash tokens (HMAC-SHA256 with a secret
salt from the PII_SALT environment variable). The same ID always gets the same token,
so joins and counts still work, but the token can't be turned back into the ID without
the salt. Birth dates and exact ages are dropped; only the age band is kept.

Checks after the build: no raw identifier columns in any rpt_ table, row counts match
the star schema, no token collisions, and every fact row still joins to its dimensions.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect
from card_analytics.quality import CheckResult

DEV_SALT = "dev-only-salt-change-me"

# Columns that must never appear in the reporting layer.
BANNED_COLUMNS = {"client_id", "account_id", "owner_client_id", "birth_date", "age_1998", "partner_account", "partner_bank"}

# source table -> (reporting table, {id column: (token column, prefix)}, columns to drop)
REPORTING = {
    "dim_client": ("rpt_dim_client", {"client_id": ("client_key", "C")}, ["birth_date", "age_1998"]),
    "dim_account": (
        "rpt_dim_account",
        {"account_id": ("account_key", "A"), "owner_client_id": ("owner_client_key", "C")},
        [],
    ),
    "fact_transactions": (
        "rpt_fact_transactions",
        {"account_id": ("account_key", "A"), "client_id": ("client_key", "C")},
        [],
    ),
    "fact_loans": ("rpt_fact_loans", {"account_id": ("account_key", "A"), "client_id": ("client_key", "C")}, []),
}

JOINS = [
    ("rpt_fact_transactions", "account_key", "rpt_dim_account", "account_key"),
    ("rpt_fact_transactions", "client_key", "rpt_dim_client", "client_key"),
    ("rpt_fact_loans", "account_key", "rpt_dim_account", "account_key"),
    ("rpt_fact_loans", "client_key", "rpt_dim_client", "client_key"),
    ("rpt_dim_account", "owner_client_key", "rpt_dim_client", "client_key"),
]


def get_salt() -> str:
    salt = os.environ.get("PII_SALT")
    if not salt:
        print("  note: PII_SALT not set, using the development salt (set it for anything shared)")
        return DEV_SALT
    return salt


def token(value, prefix: str, salt: str) -> str | None:
    if pd.isna(value):
        return None
    digest = hmac.new(salt.encode(), str(int(value)).encode(), hashlib.sha256).hexdigest()
    return f"{prefix}-{digest[:12]}"


def mask_frame(df: pd.DataFrame, ids: dict[str, tuple[str, str]], drop: list[str], salt: str) -> pd.DataFrame:
    out = df.drop(columns=drop)
    for col, (key_col, prefix) in ids.items():
        # Hash each distinct ID once, then map: fast even on a million fact rows.
        lookup = {v: token(v, prefix, salt) for v in out[col].dropna().unique()}
        position = out.columns.get_loc(col)
        values = out.pop(col).map(lookup)
        out.insert(position, key_col, values)
    return out


def build(conn, salt: str | None = None) -> dict[str, int]:
    salt = salt or get_salt()
    counts = {}
    for source, (target, ids, drop) in REPORTING.items():
        df = mask_frame(pd.read_sql(f"select * from {source}", conn), ids, drop, salt)
        df.to_sql(target, conn, if_exists="replace", index=False, chunksize=50_000)
        counts[target] = len(df)
    return counts


def scalar(conn, sql: str) -> int:
    return int(pd.read_sql(sql, conn).iloc[0, 0])


def check(conn) -> list[CheckResult]:
    results = []
    for source, (target, ids, _) in REPORTING.items():
        cols = set(pd.read_sql(f"select * from {target} limit 0", conn).columns)
        leaked = sorted(cols & BANNED_COLUMNS)
        results.append(CheckResult(f"{target}: no raw identifiers", not leaked, f"leaked={leaked}"))
        n_src, n_rpt = scalar(conn, f"select count(*) from {source}"), scalar(conn, f"select count(*) from {target}")
        results.append(CheckResult(f"{target}: row count", n_src == n_rpt, f"{n_src:,} vs {n_rpt:,}"))
        for col, (key_col, _) in ids.items():
            n_ids = scalar(conn, f"select count(distinct {col}) from {source}")
            n_keys = scalar(conn, f"select count(distinct {key_col}) from {target}")
            results.append(CheckResult(f"{target}.{key_col}: no collisions", n_ids == n_keys, f"ids={n_ids:,} tokens={n_keys:,}"))
    for child, col, parent, pcol in JOINS:
        orphans = scalar(
            conn, f"select count(*) from {child} c left join {parent} p on p.{pcol} = c.{col} where c.{col} is not null and p.{pcol} is null"
        )
        results.append(CheckResult(f"{child}.{col} -> {parent}", orphans == 0, f"orphans={orphans}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    args = parser.parse_args(argv)
    conn = connect(args.db)
    counts = build(conn)
    print("Built reporting layer (PII masked)")
    for name, n in counts.items():
        print(f"  {name:<22} {n:>10,} rows")
    results = check(conn)
    print("\nMasking checks")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name:<52} {r.detail}")
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

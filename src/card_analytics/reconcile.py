"""Month-by-month reconciliation: source (berka_*) vs star schema, to the cent.

Usage:
    python -m card_analytics.reconcile --db sqlite:///cardrisk.db
    python -m card_analytics.reconcile --db sqlite:///cardrisk.db --report docs/reconciliation-report.md

For every month it compares row counts, credit totals and debit totals between
berka_trans (the cleaned source) and fact_transactions (the model), then does the
same for loans overall. Money is compared in whole cents (integers), so floating-point
noise can't hide or invent a variance. Exits 1 if any line fails, which is what lets
the pipeline block a refresh.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect

CENTS = "cast(round({col} * 100) as integer)"

SOURCE_MONTHLY = f"""
select substr(trans_date, 1, 7)                                                   as year_month,
       count(*)                                                                   as rows,
       sum(case when direction = 'credit' then {CENTS.format(col='amount_czk')} else 0 end) as credit_cents,
       sum(case when direction = 'debit'  then {CENTS.format(col='amount_czk')} else 0 end) as debit_cents
from berka_trans
group by 1
"""

MODEL_MONTHLY = f"""
select dt.year_month,
       count(*)                                                                     as rows,
       sum(case when f.direction = 'credit' then {CENTS.format(col='f.amount_czk')} else 0 end) as credit_cents,
       sum(case when f.direction = 'debit'  then {CENTS.format(col='f.amount_czk')} else 0 end) as debit_cents
from fact_transactions f
join dim_date dt on dt.date_key = f.date_key
group by 1
"""

LOANS = """
select (select count(*) from berka_loan)                                        as source_rows,
       (select count(*) from fact_loans)                                        as model_rows,
       (select sum(cast(round(amount_czk * 100) as integer)) from berka_loan)   as source_cents,
       (select sum(cast(round(amount_czk * 100) as integer)) from fact_loans)   as model_cents
"""

METRICS = ["rows", "credit_cents", "debit_cents"]


def monthly_variance(conn) -> pd.DataFrame:
    """One row per month with source, model, variance and PASS/FAIL."""
    src = pd.read_sql(SOURCE_MONTHLY, conn).set_index("year_month")
    mod = pd.read_sql(MODEL_MONTHLY, conn).set_index("year_month")
    # Outer join so a month missing on either side shows up as a failure, not a silent gap.
    df = src.join(mod, how="outer", lsuffix="_src", rsuffix="_model").fillna(0).astype("int64")
    for m in METRICS:
        df[f"{m}_var"] = df[f"{m}_src"] - df[f"{m}_model"]
    df["status"] = (df[[f"{m}_var" for m in METRICS]] == 0).all(axis=1).map({True: "PASS", False: "FAIL"})
    return df.reset_index().sort_values("year_month").reset_index(drop=True)


def loan_variance(conn) -> pd.DataFrame:
    r = pd.read_sql(LOANS, conn).fillna(0).astype("int64").iloc[0]
    rows_var = int(r.source_rows - r.model_rows)
    cents_var = int(r.source_cents - r.model_cents)
    return pd.DataFrame(
        [
            {"check": "loans: rows", "source": int(r.source_rows), "model": int(r.model_rows), "variance": rows_var},
            {"check": "loans: amount (cents)", "source": int(r.source_cents), "model": int(r.model_cents), "variance": cents_var},
        ]
    ).assign(status=lambda d: d.variance.eq(0).map({True: "PASS", False: "FAIL"}))


def czk(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def to_markdown(monthly: pd.DataFrame, loans: pd.DataFrame) -> str:
    failed_months = monthly[monthly.status == "FAIL"]
    failed_loans = loans[loans.status == "FAIL"]
    passed = failed_months.empty and failed_loans.empty
    totals = monthly[[c for c in monthly.columns if c.endswith(("_src", "_model", "_var"))]].sum()
    lines = [
        "# Reconciliation report",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m card_analytics.reconcile`.",
        "",
        f"**Overall: {'PASS' if passed else 'FAIL'}.** "
        f"{len(monthly) - len(failed_months)}/{len(monthly)} months reconcile; "
        f"{len(loans) - len(failed_loans)}/{len(loans)} loan checks pass.",
        "",
        "Source = `berka_*` tables (cleaned raw files). Model = star schema (`fact_transactions`, `fact_loans`).",
        "Amounts are compared in whole cents, so a variance of 0.00 means an exact match.",
        "",
        "## Totals, all months",
        "",
        "| Metric | Source | Model | Variance |",
        "| --- | ---: | ---: | ---: |",
        f"| Transactions | {totals.rows_src:,} | {totals.rows_model:,} | {totals.rows_var:,} |",
        f"| Credits (CZK) | {czk(totals.credit_cents_src)} | {czk(totals.credit_cents_model)} | {czk(totals.credit_cents_var)} |",
        f"| Debits (CZK) | {czk(totals.debit_cents_src)} | {czk(totals.debit_cents_model)} | {czk(totals.debit_cents_var)} |",
        "",
        "## Loans",
        "",
        "| Check | Source | Model | Variance | Status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for r in loans.itertuples():
        fmt = czk if "cents" in r.check else (lambda v: f"{v:,}")
        lines.append(f"| {r.check.replace(' (cents)', ' (CZK)')} | {fmt(r.source)} | {fmt(r.model)} | {fmt(r.variance)} | {r.status} |")
    lines += [
        "",
        "## By month",
        "",
        "| Month | Rows (src / model) | Credits CZK (src / model) | Debits CZK (src / model) | Net variance CZK | Status |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in monthly.itertuples():
        var = r.credit_cents_var - r.debit_cents_var
        lines.append(
            f"| {r.year_month} | {r.rows_src:,} / {r.rows_model:,} | {czk(r.credit_cents_src)} / {czk(r.credit_cents_model)} | "
            f"{czk(r.debit_cents_src)} / {czk(r.debit_cents_model)} | {czk(var)} | {r.status} |"
        )
    return "\n".join(lines) + "\n"


def run(conn, report: Path | None = None) -> bool:
    monthly = monthly_variance(conn)
    loans = loan_variance(conn)
    failed = monthly[monthly.status == "FAIL"]
    print(f"Reconciliation: {len(monthly) - len(failed)}/{len(monthly)} months match to the cent")
    for r in failed.itertuples():
        print(
            f"  [FAIL] {r.year_month}: rows var={r.rows_var:,}  credits var={czk(r.credit_cents_var)}  debits var={czk(r.debit_cents_var)}"
        )
    for r in loans.itertuples():
        print(f"  [{r.status}] {r.check}: source={r.source:,} model={r.model:,} variance={r.variance:,}")
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(to_markdown(monthly, loans), encoding="utf-8")
        print(f"Report written to {report}")
    return failed.empty and (loans.status == "PASS").all()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--report", type=Path, help="write a Markdown report here")
    args = parser.parse_args(argv)
    return 0 if run(connect(args.db), args.report) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Data dictionary and lineage, generated from code so they can't drift from the database.

Usage:
    python -m card_analytics.catalog --db sqlite:///cardrisk.db

Writes docs/data-dictionary.md and docs/lineage.md. Lineage is read from the
{{ source() }} / {{ ref() }} calls in the dbt model SQL. Exits 1 if any table has a
column the dictionary doesn't describe (or describes a column that no longer exists),
so an undocumented column fails the pipeline instead of slipping into a report.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect
from card_analytics.mask import REPORTING
from card_analytics.star import BUILD_ORDER, MODELS_DIR, REF_RE, SOURCE_RE

DOCS = Path(__file__).resolve().parents[2] / "docs"

# PII classes: "-" none · "identifier" direct ID (tokenised in rpt_) · "quasi" could help re-identify (dropped or banded in rpt_)
CATALOG: dict[str, dict] = {
    "dim_date": {
        "grain": "one calendar day, 1993-01-01 to 1998-12-31",
        "columns": {
            "date_key": ("Integer date key YYYYMMDD, joins to every fact", "-"),
            "date": ("Calendar date (YYYY-MM-DD)", "-"),
            "year": ("Calendar year", "-"),
            "quarter": ("Calendar quarter 1-4", "-"),
            "month": ("Month number 1-12", "-"),
            "month_name": ("Short month name (Jan, Feb, ...)", "-"),
            "year_month": ("YYYY-MM, used for monthly reporting and reconciliation", "-"),
            "day_of_week": ("Short day name (Mon, Tue, ...)", "-"),
            "is_weekend": ("1 if Saturday or Sunday", "-"),
        },
    },
    "dim_district": {
        "grain": "one district (77)",
        "columns": {
            "district_id": ("District key", "-"),
            "district_name": ("District name", "-"),
            "region": ("Region (8), e.g. Prague, south Moravia", "-"),
            "inhabitants": ("Population", "-"),
            "urban_ratio_pct": ("Share of inhabitants living in cities, %", "-"),
            "avg_salary_czk": ("Average monthly salary, CZK", "-"),
            "unemployment_rate_96": ("Unemployment rate 1996, %", "-"),
            "entrepreneurs_per_1000": ("Entrepreneurs per 1,000 inhabitants", "-"),
            "crimes_96": ("Crimes committed in 1996", "-"),
            "crimes_per_1000": ("Crimes in 1996 per 1,000 inhabitants (derived)", "-"),
        },
    },
    "dim_client": {
        "grain": "one client (5,369)",
        "columns": {
            "client_id": ("Client key", "identifier"),
            "district_id": ("Home district", "-"),
            "gender": ("F / M, decoded from the birth number", "quasi"),
            "birth_date": ("Date of birth, decoded from the birth number", "quasi"),
            "age_1998": ("Age at the end of the data (1998)", "quasi"),
            "age_band": ("Age band: 18-24, 25-34, 35-49, 50-64, 65+", "-"),
        },
    },
    "dim_account": {
        "grain": "one account (4,500), with owner, cards and loan rolled up",
        "columns": {
            "account_id": ("Account key", "identifier"),
            "district_id": ("District of the branch", "-"),
            "statement_frequency": ("monthly, weekly or after_transaction", "-"),
            "opened_date": ("Date the account was opened", "-"),
            "owner_client_id": ("Client who owns the account", "identifier"),
            "n_users": ("Number of clients with access (owner + users)", "-"),
            "n_cards": ("Number of cards on the account", "-"),
            "card_type": ("Highest card type: junior, classic or gold (null = no card)", "-"),
            "has_loan": ("1 if the account has a loan", "-"),
            "loan_status": ("finished_ok, finished_defaulted, running_ok, running_in_debt", "-"),
            "loan_is_default": ("1 if the loan defaulted or is in debt", "-"),
        },
    },
    "fact_transactions": {
        "grain": "one transaction (1,056,320)",
        "columns": {
            "trans_id": ("Transaction key", "-"),
            "account_id": ("Account the transaction posted to", "identifier"),
            "client_id": ("Account owner", "identifier"),
            "district_id": ("District of the account", "-"),
            "date_key": ("Posting date, joins to dim_date", "-"),
            "direction": ("credit (money in) or debit (money out)", "-"),
            "operation": ("card_withdrawal, cash_deposit, cash_withdrawal, collection_from_other_bank, remittance_to_other_bank", "-"),
            "purpose": ("Payment purpose: household, insurance, pension, loan_payment, statement_fee, interest_credited, ...", "-"),
            "amount_czk": ("Amount, always positive, CZK", "-"),
            "signed_amount_czk": ("Amount with sign: + credit, - debit", "-"),
            "balance_after_czk": ("Account balance after the transaction, as printed by the bank", "-"),
            "is_overdrawn": ("1 if balance_after_czk < 0", "-"),
        },
    },
    "fact_loans": {
        "grain": "one loan (682)",
        "columns": {
            "loan_id": ("Loan key", "-"),
            "account_id": ("Account the loan is on", "identifier"),
            "client_id": ("Account owner", "identifier"),
            "district_id": ("District of the account", "-"),
            "date_key": ("Loan start date, joins to dim_date", "-"),
            "amount_czk": ("Loan amount, CZK", "-"),
            "duration_months": ("Term in months", "-"),
            "monthly_payment_czk": ("Monthly payment, CZK", "-"),
            "status": ("finished_ok, finished_defaulted, running_ok, running_in_debt", "-"),
            "is_default": ("1 if finished_defaulted or running_in_debt", "-"),
        },
    },
}

TOKEN_NOTES = {
    "client_key": "Masked client token (HMAC-SHA256 of client_id)",
    "owner_client_key": "Masked token of the owning client",
    "account_key": "Masked account token (HMAC-SHA256 of account_id)",
}


def reporting_columns(table: str) -> dict[str, tuple[str, str]]:
    """Dictionary entries for an rpt_ table, derived from its star-schema source."""
    target, ids, drop = REPORTING[table]
    cols = {}
    for col, (desc, pii) in CATALOG[table]["columns"].items():
        if col in drop:
            continue
        if col in ids:
            key = ids[col][0]
            cols[key] = (TOKEN_NOTES[key], "masked")
        else:
            cols[col] = (desc, pii)
    return cols


def drift(conn) -> list[str]:
    """Columns in the database but not in the dictionary, and the other way round."""
    problems = []
    expected = {t: set(spec["columns"]) for t, spec in CATALOG.items()}
    expected |= {REPORTING[t][0]: set(reporting_columns(t)) for t in REPORTING}
    for table, cols in expected.items():
        actual = set(pd.read_sql(f"select * from {table} limit 0", conn).columns)
        problems += [f"{table}.{c}: in database, not documented" for c in sorted(actual - cols)]
        problems += [f"{table}.{c}: documented, not in database" for c in sorted(cols - actual)]
    return problems


def dictionary_markdown() -> str:
    lines = [
        "# Data dictionary",
        "",
        "Generated by `python -m card_analytics.catalog` from `src/card_analytics/catalog.py`. Edit that file, not this one.",
        "",
        "**PII classes:** `identifier` = direct ID, tokenised in the reporting layer · "
        "`quasi` = could help re-identify someone combined with other fields; the precise ones (birth date, exact age) are dropped from the reporting layer · "
        "`masked` = token that replaces an identifier · `-` = not personal.",
        "",
        "Analysts and dashboards read the `rpt_` tables plus `dim_date` and `dim_district` (no personal data). "
        "The star-schema tables with raw IDs stay restricted.",
        "",
        "## Star schema",
    ]
    sections = [(t, spec["grain"], spec["columns"]) for t, spec in CATALOG.items()]
    for table, grain, cols in sections:
        lines += ["", f"### {table}", "", f"Grain: {grain}.", "", "| Column | Description | PII |", "| --- | --- | --- |"]
        lines += [f"| {c} | {d} | {p} |" for c, (d, p) in cols.items()]
    lines += ["", "## Reporting layer (PII masked)"]
    for source in REPORTING:
        target = REPORTING[source][0]
        lines += ["", f"### {target}", "", f"Same grain as `{source}`.", "", "| Column | Description | PII |", "| --- | --- | --- |"]
        lines += [f"| {c} | {d} | {p} |" for c, (d, p) in reporting_columns(source).items()]
    lines += [
        "",
        "## Synthetic transactions (raw_transactions)",
        "",
        "Used by the M1 generator and loader tests (`python -m card_analytics.generate`).",
        "",
        "| Column | Description | PII |",
        "| --- | --- | --- |",
        "| transaction_id | Unique transaction identifier | - |",
        "| customer_id | Customer key | identifier |",
        "| timestamp | Time the card was used (UTC) | - |",
        "| merchant_category | grocery, travel, dining, fuel, retail, online, utilities | - |",
        "| country | ISO country code of the merchant | - |",
        "| amount_usd | Transaction amount in US dollars | - |",
        "| is_fraud | Confirmed fraud label, 0/1 | - |",
    ]
    return "\n".join(lines) + "\n"


def lineage_edges(models_dir: Path = MODELS_DIR) -> list[tuple[str, str]]:
    edges = [(f"raw/{t}.csv", f"berka_{t}") for t in ["district", "account", "client", "disp", "card", "loan", "order", "trans"]]
    edges.append(("berka_trans", "dim_date"))  # generated in star.py from the transaction date range
    for model in BUILD_ORDER:
        sql = (models_dir / f"{model}.sql").read_text()
        edges += [(f"berka_{s}", model) for s in dict.fromkeys(SOURCE_RE.findall(sql))]
        edges += [(r, model) for r in dict.fromkeys(REF_RE.findall(sql))]
    edges += [(source, target) for source, (target, _, _) in REPORTING.items()]
    edges += [("rpt_fact_transactions", "BI dashboards / MI pack / models"), ("rpt_fact_loans", "BI dashboards / MI pack / models")]
    return edges


def lineage_markdown(edges: list[tuple[str, str]]) -> str:
    def node_id(name: str) -> str:
        return name.replace("/", "_").replace(".", "_").replace(" ", "_")

    def layer(name: str) -> str:
        if name.startswith("raw/"):
            return "raw"
        if name.startswith("berka_"):
            return "clean"
        if name.startswith(("dim_", "fact_")):
            return "star"
        if name.startswith("rpt_"):
            return "rpt"
        return "out"

    nodes = list(dict.fromkeys([n for e in edges for n in e]))
    groups = {
        "raw": "Raw files (git-ignored)",
        "clean": "Clean source · berka.py",
        "star": "Star schema · star.py",
        "rpt": "Reporting layer, PII masked · mask.py",
        "out": "Consumers",
    }
    lines = [
        "# Lineage",
        "",
        "Generated by `python -m card_analytics.catalog`: edges come from the `{{ source() }}` and `{{ ref() }}`",
        "calls in `dbt/models/berka/*.sql`, so this diagram updates itself when a model changes.",
        "",
        "Controls between layers: integrity checks (berka.py) → model tests + reconciliation (star.py, reconcile.py)",
        "→ masking checks (mask.py). `python -m card_analytics.pipeline` stops at the first failure.",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    for key, title in groups.items():
        lines.append(f'    subgraph {key}["{title}"]')
        lines += [f'        {node_id(n)}["{n}"]' for n in nodes if layer(n) == key]
        lines.append("    end")
    lines += [f"    {node_id(a)} --> {node_id(b)}" for a, b in edges]
    lines.append("```")
    lines += ["", "## Edges", "", "| From | To |", "| --- | --- |"] + [f"| {a} | {b} |" for a, b in edges]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--docs", type=Path, default=DOCS)
    args = parser.parse_args(argv)

    args.docs.mkdir(parents=True, exist_ok=True)
    (args.docs / "data-dictionary.md").write_text(dictionary_markdown(), encoding="utf-8")
    (args.docs / "lineage.md").write_text(lineage_markdown(lineage_edges()), encoding="utf-8")
    print(f"Wrote {args.docs / 'data-dictionary.md'} and {args.docs / 'lineage.md'}")

    problems = drift(connect(args.db))
    for p in problems:
        print(f"  [FAIL] {p}")
    print(f"Dictionary check: {'PASS' if not problems else 'FAIL'} ({len(problems)} undocumented or stale columns)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

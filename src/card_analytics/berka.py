"""Clean, translate and load the Berka Czech bank dataset (PKDD'99).

Usage:
    python -m card_analytics.berka --src data/raw/berka --db sqlite:///cardrisk.db

Each raw file is ';'-separated, in Czech, with dates as YYMMDD. This module turns
them into eight clean English tables named berka_<table> and runs integrity checks:
row counts, unique primary keys, foreign keys and valid codes.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from card_analytics.load import DEFAULT_DB, connect
from card_analytics.quality import CheckResult

# --- Czech -> English code translations (from the PKDD'99 data description) ---
FREQUENCY = {
    "POPLATEK MESICNE": "monthly",
    "POPLATEK TYDNE": "weekly",
    "POPLATEK PO OBRATU": "after_transaction",
}
TRANS_TYPE = {"PRIJEM": "credit", "VYDAJ": "debit", "VYBER": "debit"}
OPERATION = {
    "VYBER KARTOU": "card_withdrawal",
    "VKLAD": "cash_deposit",
    "PREVOD Z UCTU": "collection_from_other_bank",
    "VYBER": "cash_withdrawal",
    "PREVOD NA UCET": "remittance_to_other_bank",
}
K_SYMBOL = {
    "POJISTNE": "insurance",
    "SLUZBY": "statement_fee",
    "UROK": "interest_credited",
    "SANKC. UROK": "sanction_interest",
    "SIPO": "household",
    "DUCHOD": "pension",
    "UVER": "loan_payment",
    "LEASING": "leasing",
}
LOAN_STATUS = {
    "A": "finished_ok",
    "B": "finished_defaulted",
    "C": "running_ok",
    "D": "running_in_debt",
}
DISP_TYPE = {"OWNER": "owner", "DISPONENT": "user"}
DISTRICT_COLUMNS = [
    "district_id", "district_name", "region", "inhabitants",
    "munic_under_500", "munic_500_1999", "munic_2000_9999", "munic_10000_plus",
    "n_cities", "urban_ratio_pct", "avg_salary_czk",
    "unemployment_rate_95", "unemployment_rate_96",
    "entrepreneurs_per_1000", "crimes_95", "crimes_96",
]

TABLES = ["district", "account", "client", "disp", "card", "loan", "order", "trans"]


def _date(series: pd.Series) -> pd.Series:
    """YYMMDD (optionally followed by a time) -> datetime, all 19xx."""
    digits = series.astype(str).str.slice(0, 6)
    return pd.to_datetime("19" + digits, format="%Y%m%d")


def _read(src: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(src / f"{name}.csv", sep=";", low_memory=False)


def _map(series: pd.Series, mapping: dict, name: str) -> pd.Series:
    cleaned = series.fillna("").astype(str).str.strip()
    out = cleaned.map(mapping)
    unknown = sorted(set(cleaned[(cleaned != "") & out.isna()]))
    if unknown:
        raise ValueError(f"Unknown {name} codes: {unknown}")
    return out


def clean_district(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = DISTRICT_COLUMNS
    for col in ["unemployment_rate_95", "crimes_95"]:  # '?' marks missing values
        df[col] = pd.to_numeric(df[col].replace("?", pd.NA), errors="coerce")
    return df


def clean_account(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        frequency=_map(df["frequency"], FREQUENCY, "frequency"),
        opened_date=_date(df["date"]),
    ).drop(columns="date")


def clean_client(df: pd.DataFrame) -> pd.DataFrame:
    bn = df["birth_number"].astype(str).str.zfill(6)
    month = bn.str.slice(2, 4).astype(int)
    female = month > 50
    real_month = (month - female * 50).astype(str).str.zfill(2)
    birth = pd.to_datetime("19" + bn.str.slice(0, 2) + real_month + bn.str.slice(4, 6), format="%Y%m%d")
    return pd.DataFrame(
        {
            "client_id": df["client_id"],
            "district_id": df["district_id"],
            "gender": female.map({True: "F", False: "M"}),
            "birth_date": birth,
        }
    )


def clean_disp(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(type=_map(df["type"], DISP_TYPE, "disp type"))


def clean_card(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(issued_date=_date(df["issued"])).drop(columns="issued")


def clean_loan(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        status=_map(df["status"], LOAN_STATUS, "loan status"),
        loan_date=_date(df["date"]),
        is_default=df["status"].isin(["B", "D"]).astype(int),
    ).drop(columns="date").rename(columns={"amount": "amount_czk", "payments": "monthly_payment_czk"})


def clean_order(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        k_symbol=_map(df["k_symbol"].replace(" ", ""), K_SYMBOL, "order k_symbol"),
    ).rename(columns={"amount": "amount_czk", "bank_to": "partner_bank", "account_to": "partner_account"})


def clean_trans(df: pd.DataFrame) -> pd.DataFrame:
    direction = _map(df["type"], TRANS_TYPE, "trans type")
    out = pd.DataFrame(
        {
            "trans_id": df["trans_id"],
            "account_id": df["account_id"],
            "trans_date": _date(df["date"]),
            "direction": direction,
            "operation": _map(df["operation"], OPERATION, "operation"),
            "amount_czk": df["amount"],
            "signed_amount_czk": df["amount"].where(direction == "credit", -df["amount"]),
            "balance_after_czk": df["balance"],
            "k_symbol": _map(df["k_symbol"].replace(" ", ""), K_SYMBOL, "trans k_symbol"),
            "partner_bank": df["bank"],
            "partner_account": df["account"],
        }
    )
    return out


CLEANERS = {
    "district": clean_district,
    "account": clean_account,
    "client": clean_client,
    "disp": clean_disp,
    "card": clean_card,
    "loan": clean_loan,
    "order": clean_order,
    "trans": clean_trans,
}

PRIMARY_KEYS = {
    "district": "district_id", "account": "account_id", "client": "client_id",
    "disp": "disp_id", "card": "card_id", "loan": "loan_id",
    "order": "order_id", "trans": "trans_id",
}

FOREIGN_KEYS = [  # (child table, column, parent table, parent column)
    ("account", "district_id", "district", "district_id"),
    ("client", "district_id", "district", "district_id"),
    ("disp", "client_id", "client", "client_id"),
    ("disp", "account_id", "account", "account_id"),
    ("card", "disp_id", "disp", "disp_id"),
    ("loan", "account_id", "account", "account_id"),
    ("order", "account_id", "account", "account_id"),
    ("trans", "account_id", "account", "account_id"),
]


def clean_all(src: Path) -> dict[str, pd.DataFrame]:
    return {name: CLEANERS[name](_read(src, name)) for name in TABLES}


def check_all(raw_counts: dict[str, int], tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, df in tables.items():
        results.append(CheckResult(f"{name}: row_count", len(df) == raw_counts[name], f"raw={raw_counts[name]:,} clean={len(df):,}"))
        dupes = int(df[PRIMARY_KEYS[name]].duplicated().sum())
        results.append(CheckResult(f"{name}: unique_{PRIMARY_KEYS[name]}", dupes == 0, f"duplicates={dupes}"))
    for child, col, parent, pcol in FOREIGN_KEYS:
        orphans = int((~tables[child][col].isin(tables[parent][pcol])).sum())
        results.append(CheckResult(f"{child}.{col} -> {parent}", orphans == 0, f"orphans={orphans}"))
    t = tables["trans"]
    in_range = t["trans_date"].between("1993-01-01", "1998-12-31").all()
    results.append(CheckResult("trans: dates_1993_1998", bool(in_range), f"min={t['trans_date'].min().date()} max={t['trans_date'].max().date()}"))
    positive = int((t["amount_czk"] < 0).sum())
    results.append(CheckResult("trans: non_negative_amounts", positive == 0, f"negative={positive}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=Path("data/raw/berka"))
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    args = parser.parse_args(argv)

    raw_counts = {name: len(_read(args.src, name)) for name in TABLES}
    tables = clean_all(args.src)
    conn = connect(args.db)
    for name, df in tables.items():
        df.to_sql(f"berka_{name}", conn, if_exists="replace", index=False, chunksize=20000)
        print(f"Loaded berka_{name:<9} {len(df):>10,} rows")

    results = check_all(raw_counts, tables)
    print("\nIntegrity report")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name:<32} {r.detail}")
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

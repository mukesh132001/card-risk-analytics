"""Data-quality and reconciliation checks, the controls a bank MI team signs off on."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS = [
    "transaction_id",
    "customer_id",
    "timestamp",
    "merchant_category",
    "country",
    "amount_usd",
    "is_fraud",
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def run_checks(df: pd.DataFrame) -> list[CheckResult]:
    results = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    results.append(CheckResult("required_columns", not missing, f"missing={missing}"))
    if missing:
        return results

    nulls = int(df[REQUIRED_COLUMNS].isna().sum().sum())
    results.append(CheckResult("no_nulls", nulls == 0, f"null_cells={nulls}"))

    dupes = int(df["transaction_id"].duplicated().sum())
    results.append(CheckResult("unique_transaction_id", dupes == 0, f"duplicates={dupes}"))

    negative = int((df["amount_usd"] <= 0).sum())
    results.append(CheckResult("positive_amounts", negative == 0, f"non_positive={negative}"))

    bad_flags = int((~df["is_fraud"].isin([0, 1])).sum())
    results.append(CheckResult("fraud_flag_binary", bad_flags == 0, f"invalid={bad_flags}"))
    return results


def reconcile(source: pd.DataFrame, model: pd.DataFrame, column: str = "amount_usd") -> CheckResult:
    """Totals in the source and the modeled table must match to the cent."""
    src_total = round(float(source[column].sum()), 2)
    model_total = round(float(model[column].sum()), 2)
    variance = round(src_total - model_total, 2)
    return CheckResult(
        "reconciliation",
        variance == 0,
        f"source={src_total:.2f} model={model_total:.2f} variance={variance:.2f}",
    )

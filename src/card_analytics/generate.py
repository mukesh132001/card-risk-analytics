"""Generate a synthetic card-transaction dataset.

Usage:
    python -m card_analytics.generate --rows 5000 --out data/sample/transactions.csv
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

CATEGORIES = ["grocery", "travel", "dining", "fuel", "retail", "online", "utilities"]
COUNTRIES = ["US", "US", "US", "US", "GB", "CA", "MX", "IN"]


def generate(rows: int, customers: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1)
    records = []
    for i in range(rows):
        category = rng.choice(CATEGORIES)
        base = {"travel": 400, "utilities": 120, "fuel": 50}.get(category, 60)
        amount = round(max(1.0, rng.gauss(base, base * 0.6)), 2)
        is_fraud = rng.random() < 0.01
        if is_fraud:
            amount = round(amount * rng.uniform(4, 12), 2)
        records.append(
            {
                "transaction_id": f"T{i:07d}",
                "customer_id": f"C{rng.randrange(customers):05d}",
                "timestamp": start + timedelta(minutes=rng.randrange(60 * 24 * 300)),
                "merchant_category": category,
                "country": rng.choice(COUNTRIES),
                "amount_usd": amount,
                "is_fraud": int(is_fraud),
            }
        )
    return pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--out", type=Path, default=Path("data/sample/transactions.csv"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df = generate(args.rows)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()

"""Customer segmentation: k-means on account behaviour from the reporting layer.

Usage:
    python -m card_analytics.segment --db sqlite:///cardrisk.db

Builds one feature row per account (spend, frequency, balance, money-in/out mix),
standardises it, scores k = 2..8 by silhouette, and picks the best k between 3 and 6:
few enough segments for a business to act on, enough to be more than "big vs small".
Then it profiles each segment. Writes:
    rpt_account_segments     account_key -> segment, for dashboards
    docs/segmentation.md     how k was chosen + a profile of every segment
Only masked rpt_ tables are read.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from card_analytics.load import DEFAULT_DB, connect

SEED = 42
K_RANGE = range(2, 9)
MIN_K, MAX_K = 3, 6

FEATURES_SQL = """
with t as (
    select f.account_key,
           substr(cast(f.date_key as text), 1, 6)                                        as ym,
           f.direction, f.operation, f.purpose, f.amount_czk, f.balance_after_czk, f.is_overdrawn
    from rpt_fact_transactions f
)
select account_key,
       count(distinct ym)                                                                 as months_active,
       sum(case when direction = 'credit' then amount_czk else 0 end)                     as money_in,
       sum(case when direction = 'debit'  then amount_czk else 0 end)                     as money_out,
       count(*)                                                                            as transactions,
       avg(balance_after_czk)                                                              as avg_balance,
       avg(is_overdrawn)                                                                   as overdrawn_rate,
       sum(case when operation = 'cash_withdrawal'          then amount_czk else 0 end)    as cash_out,
       sum(case when operation = 'card_withdrawal'          then amount_czk else 0 end)    as card_out,
       sum(case when operation = 'remittance_to_other_bank' then amount_czk else 0 end)    as transfers_out,
       sum(case when purpose   = 'loan_payment'             then amount_czk else 0 end)    as loan_payments,
       sum(case when purpose   = 'pension'                  then amount_czk else 0 end)    as pension_in,
       sum(case when operation = 'cash_deposit'             then amount_czk else 0 end)    as cash_in
from t
group by account_key
"""

# Model inputs: (column, plain-English label)
FEATURES = [
    ("log_monthly_in", "money in per month (log)"),
    ("log_monthly_out", "money out per month (log)"),
    ("txns_per_month", "transactions per month"),
    ("log_avg_balance", "average balance (log)"),
    ("overdrawn_rate", "share of transactions overdrawn"),
    ("cash_share", "cash withdrawals, share of money out"),
    ("card_share", "card spend, share of money out"),
    ("transfer_share", "transfers to other banks, share of money out"),
    ("loan_share", "loan repayments, share of money out"),
    ("pension_share", "pension, share of money in"),
    ("cash_in_share", "cash deposits, share of money in"),
    ("has_card", "has a card"),
]


def build_features(conn) -> pd.DataFrame:
    raw = pd.read_sql(FEATURES_SQL, conn)
    cards = pd.read_sql("select account_key, case when n_cards > 0 then 1 else 0 end as has_card, loan_is_default, has_loan from rpt_dim_account", conn)
    df = raw.merge(cards, on="account_key", how="left")
    months = df.months_active.clip(lower=1)
    out_total = df.money_out.replace(0, np.nan)
    in_total = df.money_in.replace(0, np.nan)
    feats = pd.DataFrame(
        {
            "account_key": df.account_key,
            "monthly_in": df.money_in / months,
            "monthly_out": df.money_out / months,
            "avg_balance": df.avg_balance,
            "txns_per_month": df.transactions / months,
            "overdrawn_rate": df.overdrawn_rate,
            "cash_share": (df.cash_out / out_total).fillna(0),
            "card_share": (df.card_out / out_total).fillna(0),
            "transfer_share": (df.transfers_out / out_total).fillna(0),
            "loan_share": (df.loan_payments / out_total).fillna(0),
            "pension_share": (df.pension_in / in_total).fillna(0),
            "cash_in_share": (df.cash_in / in_total).fillna(0),
            "has_card": df.has_card.fillna(0).astype(int),
            "has_loan": df.has_loan.fillna(0).astype(int),
            "loan_is_default": df.loan_is_default.fillna(0).astype(int),
        }
    )
    # Money is heavily right-skewed: log it so a few rich accounts don't dominate the distances.
    feats["log_monthly_in"] = np.log1p(feats.monthly_in)
    feats["log_monthly_out"] = np.log1p(feats.monthly_out)
    feats["log_avg_balance"] = np.log1p(feats.avg_balance.clip(lower=0))
    return feats


def choose_k(X: np.ndarray, k_range=K_RANGE, min_k: int = MIN_K, max_k: int = MAX_K) -> tuple[int, pd.DataFrame]:
    rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(X)
        rows.append({"k": k, "inertia": km.inertia_, "silhouette": silhouette_score(X, km.labels_, random_state=SEED)})
    scores = pd.DataFrame(rows)
    best = scores[scores.k.between(min_k, max_k)].sort_values("silhouette", ascending=False).iloc[0]
    return int(best.k), scores


def fit(feats: pd.DataFrame, k: int | None = None):
    cols = [c for c, _ in FEATURES]
    X = StandardScaler().fit_transform(feats[cols])
    scores = None
    if k is None:
        k, scores = choose_k(X)
    km = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(X)
    # Number segments by money in per month (1 = highest) so labels are stable across runs.
    order = feats.groupby(km.labels_).monthly_in.mean().sort_values(ascending=False).index
    relabel = {old: new for new, old in enumerate(order, start=1)}
    labels = pd.Series(km.labels_).map(relabel).to_numpy()
    return labels, k, scores, X


def profile(feats: pd.DataFrame, labels: np.ndarray, X: np.ndarray) -> tuple[pd.DataFrame, dict[int, str]]:
    df = feats.assign(segment=labels)
    prof = df.groupby("segment").agg(
        accounts=("account_key", "count"),
        monthly_in=("monthly_in", "mean"),
        monthly_out=("monthly_out", "mean"),
        avg_balance=("avg_balance", "mean"),
        txns_per_month=("txns_per_month", "mean"),
        card_holders=("has_card", "mean"),
        cash_share=("cash_share", "mean"),
        pension_share=("pension_share", "mean"),
        overdrawn_rate=("overdrawn_rate", "mean"),
        loan_holders=("has_loan", "mean"),
        loans=("has_loan", "sum"),
        defaults=("loan_is_default", "sum"),
    )
    prof["share_of_accounts"] = prof.accounts / prof.accounts.sum()
    prof["default_rate"] = np.where(prof.loans > 0, prof.defaults / prof.loans.where(prof.loans > 0, 1), np.nan)

    # Describe each segment by the features where its average sits furthest from the overall average (in std units).
    z = pd.DataFrame(X, columns=[c for c, _ in FEATURES]).assign(segment=labels).groupby("segment").mean()
    names = dict(FEATURES)
    descriptions = {}
    for seg, row in z.iterrows():
        top = row.abs().sort_values(ascending=False).index[:3]
        parts = [f"{'higher' if row[c] > 0 else 'lower'} {names[c]} ({row[c]:+.1f} sd)" for c in top]
        descriptions[int(seg)] = "; ".join(parts)
    return prof, descriptions


def to_markdown(k: int, scores: pd.DataFrame | None, prof: pd.DataFrame, descriptions: dict[int, str]) -> str:
    lines = [
        "# Customer segmentation",
        "",
        "Generated by `python -m card_analytics.segment`. K-means on 12 standardised behaviour features per account",
        "(money in/out per month, transactions per month, balance, overdrafts, cash/card/transfer/loan mix, pension and cash income, card ownership),",
        "built from the PII-masked reporting layer. Segment 1 has the highest money in per month.",
        "",
    ]
    if scores is not None:
        lines += [
            "## Choosing k",
            "",
            f"Silhouette score (higher = tighter, better-separated groups) for k = 2..8. Picked **k = {k}**, the best score between {MIN_K} and {MAX_K} segments (a usable number for a business).",
            "",
            "| k | Silhouette | Inertia |",
            "| ---: | ---: | ---: |",
        ]
        lines += [f"| {int(r.k)}{' ✓' if r.k == k else ''} | {r.silhouette:.3f} | {r.inertia:,.0f} |" for r in scores.itertuples()]
        lines.append("")
    lines += [
        "## Segment profiles",
        "",
        "| Segment | Accounts | Share | Money in / month (CZK) | Money out / month (CZK) | Avg balance (CZK) | Txns / month | Card holders | Cash share of spend | Pension share of income | Loan holders | Loan default rate |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for seg, r in prof.iterrows():
        dr = "-" if pd.isna(r.default_rate) else f"{r.default_rate:.1%} ({int(r.defaults)}/{int(r.loans)})"
        lines.append(
            f"| {seg} | {int(r.accounts):,} | {r.share_of_accounts:.0%} | {r.monthly_in:,.0f} | {r.monthly_out:,.0f} | {r.avg_balance:,.0f} | "
            f"{r.txns_per_month:.1f} | {r.card_holders:.0%} | {r.cash_share:.0%} | {r.pension_share:.0%} | {r.loan_holders:.0%} | {dr} |"
        )
    lines += ["", "## What sets each segment apart", ""]
    lines += [f"- **Segment {seg}:** {d}." for seg, d in descriptions.items()]
    lines += [
        "",
        "`sd` = standard deviations from the all-account average on that feature. Segment labels are in `rpt_account_segments` for dashboards.",
    ]
    return "\n".join(lines) + "\n"


MIN_ACCOUNTS = 50


def run(conn, docs: Path | None = None, k: int | None = None) -> pd.DataFrame:
    feats = build_features(conn)
    if len(feats) < MIN_ACCOUNTS:
        print(f"Only {len(feats)} accounts with transactions: too few to segment, skipped.")
        return pd.DataFrame()
    labels, k, scores, X = fit(feats, k)
    pd.DataFrame({"account_key": feats.account_key, "segment": labels}).to_sql("rpt_account_segments", conn, if_exists="replace", index=False)
    prof, desc = profile(feats, labels, X)
    if docs:
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "segmentation.md").write_text(to_markdown(k, scores, prof, desc), encoding="utf-8")
    return prof


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    parser.add_argument("--k", type=int, help="force a number of segments instead of choosing by silhouette")
    args = parser.parse_args(argv)
    prof = run(connect(args.db), args.docs, args.k)
    if prof.empty:
        return 0
    cols = ["accounts", "monthly_in", "avg_balance", "txns_per_month", "card_holders", "default_rate"]
    print(prof[cols].round(3).to_string())
    print(f"\nSegments written to rpt_account_segments; profile in {args.docs / 'segmentation.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

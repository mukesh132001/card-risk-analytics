"""Loan default model: baseline -> logistic regression -> gradient boosting, with a model card.

Usage:
    python -m card_analytics.model --db sqlite:///cardrisk.db

Berka has no fraud labels, so the risk model predicts loan default (status
finished_defaulted or running_in_debt) — a credit-risk use case.

No look-ahead: every feature is built only from transactions dated BEFORE the loan
started, plus the loan's own terms and its district. Card ownership and customer
segment are left out on purpose, because they are measured at the end of the data
and would leak the future into the model.

Models are compared with repeated stratified 5-fold cross-validation (682 loans is too
few for a single train/test split to be reliable). The simplest model within 0.02 PR-AUC
of the best one wins: a bank has to explain its credit models. The alert threshold is chosen on
out-of-fold predictions: the highest precision that still catches at least 75% of
defaults. Writes docs/model-card.md, docs/images/pr_curve.png and rpt_loan_scores.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from card_analytics.load import DEFAULT_DB, connect

SEED = 42
TARGET_RECALL = 0.75
TOLERANCE = 0.02  # prefer a simpler model if it is this close to the best PR-AUC

FEATURES_SQL = """
with loans as (
    select l.loan_id, l.account_key, l.date_key as loan_date_key, l.amount_czk, l.duration_months,
           l.monthly_payment_czk, l.is_default, l.district_id
    from rpt_fact_loans l
),
hist as (
    -- strictly before the loan start date: no look-ahead
    select l.loan_id, f.*
    from loans l
    join rpt_fact_transactions f on f.account_key = l.account_key and f.date_key < l.loan_date_key
)
select l.loan_id,
       l.account_key,
       l.loan_date_key,
       l.is_default,
       l.amount_czk,
       l.duration_months,
       l.monthly_payment_czk,
       d.avg_salary_czk,
       d.unemployment_rate_96,
       d.crimes_per_1000,
       count(distinct substr(cast(h.date_key as text), 1, 6))                         as months_history,
       count(h.trans_id)                                                              as n_txns,
       sum(case when h.direction = 'credit' then h.amount_czk else 0 end)             as money_in,
       sum(case when h.direction = 'debit'  then h.amount_czk else 0 end)             as money_out,
       avg(h.balance_after_czk)                                                       as avg_balance,
       min(h.balance_after_czk)                                                       as min_balance,
       sum(h.is_overdrawn)                                                            as n_overdrawn,
       sum(case when h.purpose = 'sanction_interest' then 1 else 0 end)               as n_penalty_interest,
       sum(case when h.operation = 'cash_withdrawal' then h.amount_czk else 0 end)    as cash_out
from loans l
join dim_district d on d.district_id = l.district_id
left join hist h on h.loan_id = l.loan_id
group by l.loan_id
order by l.loan_id
"""

FEATURES = [
    ("loan_amount_log", "Loan amount (log CZK)"),
    ("duration_months", "Loan term, months"),
    ("payment_to_income", "Monthly payment / average monthly money in before the loan"),
    ("months_history", "Months of account history before the loan"),
    ("monthly_in_log", "Average monthly money in before the loan (log)"),
    ("avg_balance_log", "Average balance before the loan (log)"),
    ("min_balance", "Lowest balance before the loan, CZK"),
    ("balance_to_payment", "Average balance / monthly payment"),
    ("overdrawn_rate", "Share of pre-loan transactions that left the account overdrawn"),
    ("had_penalty_interest", "Ever charged penalty (sanction) interest before the loan"),
    ("cash_share", "Cash withdrawals, share of money out before the loan"),
    ("district_salary", "District average salary, CZK"),
    ("district_unemployment", "District unemployment rate 1996, %"),
    ("district_crime", "District crimes per 1,000 inhabitants"),
]


def build_features(conn) -> pd.DataFrame:
    df = pd.read_sql(FEATURES_SQL, conn)
    months = df.months_history.clip(lower=1)
    monthly_in = df.money_in / months
    out = pd.DataFrame(
        {
            "loan_id": df.loan_id,
            "account_key": df.account_key,
            "loan_date_key": df.loan_date_key,
            "is_default": df.is_default.astype(int),
            "loan_amount_log": np.log1p(df.amount_czk),
            "duration_months": df.duration_months,
            "payment_to_income": df.monthly_payment_czk / monthly_in.replace(0, np.nan),
            "months_history": df.months_history,
            "monthly_in_log": np.log1p(monthly_in),
            "avg_balance_log": np.log1p(df.avg_balance.clip(lower=0)),
            "min_balance": df.min_balance,
            "balance_to_payment": df.avg_balance / df.monthly_payment_czk.replace(0, np.nan),
            "overdrawn_rate": df.n_overdrawn / df.n_txns.replace(0, np.nan),
            "had_penalty_interest": (df.n_penalty_interest > 0).astype(int),
            "cash_share": df.cash_out / df.money_out.replace(0, np.nan),
            "district_salary": df.avg_salary_czk,
            "district_unemployment": df.unemployment_rate_96,
            "district_crime": df.crimes_per_1000,
        }
    )
    cols = [c for c, _ in FEATURES]
    out[cols] = out[cols].replace([np.inf, -np.inf], np.nan)
    out[cols] = out[cols].fillna(out[cols].median())
    return out


def candidates() -> dict:
    return {
        "Baseline (always predict the default rate)": DummyClassifier(strategy="prior"),
        "Logistic regression": make_pipeline(
            StandardScaler(), LogisticRegression(class_weight="balanced", C=0.5, max_iter=2000, random_state=SEED)
        ),
        "Gradient boosting": GradientBoostingClassifier(
            n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=SEED
        ),
    }


def compare(X: np.ndarray, y: np.ndarray, n_repeats: int = 5) -> pd.DataFrame:
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=n_repeats, random_state=SEED)
    rows = []
    for name, model in candidates().items():
        pr, roc = [], []
        for train, test in cv.split(X, y):
            m = clone(model).fit(X[train], y[train])
            p = m.predict_proba(X[test])[:, 1]
            pr.append(average_precision_score(y[test], p))
            roc.append(roc_auc_score(y[test], p))
        rows.append({"model": name, "pr_auc": np.mean(pr), "pr_auc_sd": np.std(pr), "roc_auc": np.mean(roc), "roc_auc_sd": np.std(roc)})
    return pd.DataFrame(rows)


def pick_model(scores: pd.DataFrame, tolerance: float = TOLERANCE) -> str:
    """Simplest model (candidates are listed simplest first) within `tolerance` of the best PR-AUC, baseline excluded."""
    real = scores.iloc[1:]
    best = real.pr_auc.max()
    return real[real.pr_auc >= best - tolerance].model.iloc[0]


def choose_threshold(y: np.ndarray, p: np.ndarray, target_recall: float = TARGET_RECALL) -> dict:
    precision, recall, thresholds = precision_recall_curve(y, p)
    # precision/recall have one more element than thresholds; drop the final (recall=0) point.
    ok = np.where(recall[:-1] >= target_recall)[0]
    i = ok[np.argmax(precision[ok])]
    t = float(thresholds[i])
    flagged = p >= t
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    tn = int((~flagged & (y == 0)).sum())
    return {"threshold": t, "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "flag_rate": float(flagged.mean())}


def importance(model, X: np.ndarray, names: list[str]) -> pd.Series:
    """Tree models: feature importance. Logistic regression: standardised coefficient (sign = direction)."""
    if hasattr(model, "feature_importances_"):
        imp = pd.Series(model.feature_importances_, index=names)
    else:
        imp = pd.Series(model[-1].coef_[0], index=names)
    return imp.reindex(imp.abs().sort_values(ascending=False).index)


def plot_pr(y, oof: dict[str, np.ndarray], chosen: str, thr: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 4.5), dpi=150)
    colors = {"Logistic regression": "#2a6fdb", "Gradient boosting": "#d9822b"}
    for name, p in oof.items():
        if name not in colors:
            continue
        precision, recall, _ = precision_recall_curve(y, p)
        ax.plot(recall, precision, color=colors[name], lw=2, label=f"{name} (PR-AUC {average_precision_score(y, p):.2f})")
    ax.axhline(y.mean(), color="#888888", ls="--", lw=1, label=f"Baseline: default rate {y.mean():.1%}")
    ax.scatter([thr["recall"]], [thr["precision"]], color="black", zorder=5, s=30)
    ax.annotate(f"chosen threshold {thr['threshold']:.2f}\nrecall {thr['recall']:.0%}, precision {thr['precision']:.0%}",
                (thr["recall"], thr["precision"]), xytext=(10, 15), textcoords="offset points", fontsize=8)
    ax.set_xlabel("Recall (share of defaults caught)")
    ax.set_ylabel("Precision (share of flags that default)")
    ax.set_title("Loan default model: precision-recall, out-of-fold", fontsize=10)
    ax.set_xlim(0, 1.01)
    ax.set_ylim(0, 1.01)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def model_card(n: int, n_pos: int, scores: pd.DataFrame, chosen: str, thr: dict, imp: pd.Series, by_year: pd.DataFrame) -> str:
    labels = dict(FEATURES)
    best = scores.set_index("model").loc[chosen]
    base = scores.set_index("model").iloc[0]
    lines = [
        "# Model card: loan default model",
        "",
        "Generated by `python -m card_analytics.model`.",
        "",
        "## What it does",
        "",
        "Scores each loan with the probability that it defaults (status `finished_defaulted` or `running_in_debt`),",
        "using only what the bank knew **on the day the loan started**: the loan's terms, the account's transaction",
        "history before that day, and district statistics. Loans above the threshold are flagged for review.",
        "",
        "Intended use: prioritising loans for early review by a credit-risk team. **Not** for approving or declining",
        "loans on its own: it was trained on 682 historical loans from one bank in the 1990s.",
        "",
        "## Data",
        "",
        f"- {n} loans, {n_pos} defaults ({n_pos / n:.1%}). Source: masked reporting layer (`rpt_fact_loans`, `rpt_fact_transactions`).",
        "- Features use transactions **strictly before** the loan start date (tested in `tests/test_model.py`).",
        "- Left out on purpose: card ownership and customer segment. Both are measured at the end of the data, after many",
        "  loans started, so using them would leak the future. (The README's \"card holders default less\" finding is a",
        "  correlation at the end of the data, not something the model can use at the start of a loan.)",
        "",
        "## Models compared",
        "",
        "Repeated stratified 5-fold cross-validation (5 × 5 = 25 fits per model). PR-AUC is the headline metric because",
        f"defaults are rare: a model that knows nothing scores the default rate ({base.pr_auc:.2f}).",
        "",
        "| Model | PR-AUC | ROC-AUC |",
        "| --- | ---: | ---: |",
    ]
    for r in scores.itertuples():
        mark = " ✓" if r.model == chosen else ""
        lines.append(f"| {r.model}{mark} | {r.pr_auc:.3f} ± {r.pr_auc_sd:.3f} | {r.roc_auc:.3f} ± {r.roc_auc_sd:.3f} |")
    lines += [
        "",
        f"Chosen: **{chosen}**, PR-AUC about {best.pr_auc / base.pr_auc:.1f}× the baseline. Rule: the simplest model within",
        f"{TOLERANCE} PR-AUC of the best wins, because a bank has to be able to explain a credit model to customers and regulators.",
        "",
        "## Threshold",
        "",
        f"Picked on out-of-fold predictions: the highest precision that still catches at least {TARGET_RECALL:.0%} of defaults.",
        "Missing a default (a loss) costs the bank far more than an extra review, so recall comes first.",
        "",
        "![Precision-recall curve](images/pr_curve.png)",
        "",
        f"At threshold **{thr['threshold']:.2f}** the model flags {thr['flag_rate']:.0%} of loans:",
        "",
        "| | Flagged | Not flagged |",
        "| --- | ---: | ---: |",
        f"| Defaulted | {thr['tp']} (caught) | {thr['fn']} (missed) |",
        f"| Did not default | {thr['fp']} (extra reviews) | {thr['tn']} |",
        "",
        f"Recall {thr['recall']:.0%}, precision {thr['precision']:.0%}: about 1 in {1 / max(thr['precision'], 1e-9):.0f} flagged loans defaults,",
        f"versus 1 in {1 / (n_pos / n):.0f} if loans were reviewed at random.",
        "",
        "## What drives the score",
        "",
    ]
    if (imp < 0).any():
        lines += [
            "Standardised logistic-regression coefficients: the change in log-odds of default for a one-standard-deviation",
            "increase in the feature, holding the others fixed. Positive = raises risk. Income, balance and payment size",
            "move together, so read them as a group: for the same balance and payment, more money flowing through the",
            "account (spent rather than saved) is a warning sign.",
            "",
            "| Feature | Coefficient | Effect |",
            "| --- | ---: | --- |",
        ]
        lines += [f"| {labels[f]} | {v:+.2f} | {'raises risk' if v > 0 else 'lowers risk'} |" for f, v in imp.head(8).items()]
    else:
        lines += ["| Feature | Importance |", "| --- | ---: |"]
        lines += [f"| {labels[f]} | {v:.3f} |" for f, v in imp.head(8).items()]
    lines += [
        "",
        "## Limitations",
        "",
        "- **Small sample.** 682 loans and "
        f"{n_pos} defaults: the ± ranges above are real uncertainty. Re-validate before any real use.",
        "- **Immature recent loans.** Loans that started in 1998 have had little time to go bad, so their default rate",
        "  is understated (labels are right-censored):",
        "",
        "| Start year | Loans | Default rate |",
        "| --- | ---: | ---: |",
    ]
    lines += [f"| {int(r.year)} | {int(r.loans)} | {r.default_rate:.1%} |" for r in by_year.itertuples()]
    lines += [
        "",
        "- **Old, single-bank data** (Czech bank, 1993–1998). Behaviour and economics differ today.",
        "- **Fairness.** No gender or age is used as a feature. District statistics are used and could act as a proxy",
        "  for where people live; check outcomes by district before any real use.",
        "- Scores for every loan are in `rpt_loan_scores` (masked account keys only).",
    ]
    return "\n".join(lines) + "\n"


def run(conn, docs: Path | None = Path("docs"), n_repeats: int = 5) -> dict:
    feats = build_features(conn)
    names = [c for c, _ in FEATURES]
    X, y = feats[names].to_numpy(float), feats.is_default.to_numpy(int)

    scores = compare(X, y, n_repeats)
    chosen = pick_model(scores)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    oof = {name: cross_val_predict(m, X, y, cv=cv, method="predict_proba")[:, 1] for name, m in candidates().items()}
    thr = choose_threshold(y, oof[chosen])

    final = clone(candidates()[chosen]).fit(X, y)
    imp = importance(final, X, names)

    pd.DataFrame(
        {"loan_id": feats.loan_id, "account_key": feats.account_key, "default_score": oof[chosen],
         "flagged": (oof[chosen] >= thr["threshold"]).astype(int), "is_default": y}
    ).to_sql("rpt_loan_scores", conn, if_exists="replace", index=False)

    by_year = feats.assign(year=feats.loan_date_key // 10000).groupby("year").is_default.agg(loans="count", default_rate="mean").reset_index()
    if docs:
        plot_pr(y, oof, chosen, thr, docs / "images" / "pr_curve.png")
        (docs / "model-card.md").write_text(model_card(len(y), int(y.sum()), scores, chosen, thr, imp, by_year), encoding="utf-8")
    return {"scores": scores, "chosen": chosen, "threshold": thr, "importance": imp}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args(argv)
    conn = connect(args.db)
    n = int(pd.read_sql("select count(*) n, sum(is_default) d from rpt_fact_loans", conn).n[0])
    d = int(pd.read_sql("select coalesce(sum(is_default), 0) d from rpt_fact_loans", conn).d[0])
    if n < 50 or d < 10:
        print(f"Only {n} loans / {d} defaults: too few to train a model, skipped.")
        return 0
    out = run(conn, args.docs)
    print(out["scores"].round(3).to_string(index=False))
    t = out["threshold"]
    print(f"\nChosen: {out['chosen']}; threshold {t['threshold']:.2f} -> recall {t['recall']:.0%}, precision {t['precision']:.0%}, flags {t['flag_rate']:.0%} of loans")
    print(f"Model card: {args.docs / 'model-card.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Findings memo: two pages, three recommendations with money impact.

Usage:
    python -m card_analytics.findings --db sqlite:///cardrisk.db

Every number in docs/findings-memo.md is computed here from the reporting layer
(rpt_ tables, segments and loan scores), so the memo can't drift from the data.
Run it after the pipeline. Impact assumptions are constants below, stated in the memo.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from card_analytics import model, reconcile
from card_analytics.load import DEFAULT_DB, connect

# ---- impact assumptions (shown in the memo) ----
LGD = 0.45                # loss given default: Basel II foundation-IRB supervisory value, senior unsecured corporate claims
NET_MARGIN = 0.03         # illustrative net interest margin per year on a good loan
RECOVERY_STEP = 0.10      # "each extra 10% recovered" sensitivity
WATCHLIST_SCENARIO = 0.25 # illustrative share of watch-list loans that go on to default


def czk_m(x: float) -> str:
    return f"CZK {x / 1e6:,.1f}m"


def compute(conn) -> dict:
    loans = pd.read_sql(
        """select l.loan_id, l.amount_czk, l.duration_months, l.status, l.is_default, s.flagged, s.default_score
           from rpt_fact_loans l join rpt_loan_scores s using (loan_id)""",
        conn,
    )
    feats = model.build_features(conn)[["loan_id", "had_penalty_interest", "overdrawn_rate"]]
    loans = loans.merge(feats, on="loan_id")
    loans["warning_sign"] = ((loans.had_penalty_interest == 1) | (loans.overdrawn_rate > 0)).astype(int)

    n, d = len(loans), int(loans.is_default.sum())
    f = {
        "loans": n,
        "defaults": d,
        "default_rate": d / n,
        "book_czk": loans.amount_czk.sum(),
        "defaulted_czk": loans.loc[loans.is_default == 1, "amount_czk"].sum(),
    }

    # R1: application rule — pre-loan overdraft or penalty interest
    w = loans[loans.warning_sign == 1]
    rest = loans[loans.warning_sign == 0]
    good_w = w[w.is_default == 0]
    f |= {
        "r1_loans": len(w),
        "r1_defaults": int(w.is_default.sum()),
        "r1_rate": w.is_default.mean(),
        "r1_rest_rate": rest.is_default.mean(),
        "r1_defaulted_czk": w.loc[w.is_default == 1, "amount_czk"].sum(),
        "r1_good_loans": len(good_w),
        "r1_good_czk": good_w.amount_czk.sum(),
        "r1_margin_forgone": (good_w.amount_czk * NET_MARGIN * good_w.duration_months / 12).sum(),
    }
    f["r1_loss_avoided"] = f["r1_defaulted_czk"] * LGD
    f["r1_share_of_defaulted"] = f["r1_defaulted_czk"] / f["defaulted_czk"]

    # R2: collections — live loans already in arrears, ranked by model score
    debt = loans[loans.status == "running_in_debt"]
    f |= {
        "r2_loans": len(debt),
        "r2_czk": debt.amount_czk.sum(),
        "r2_flagged": int(debt.flagged.sum()),
    }
    f["r2_expected_loss"] = f["r2_czk"] * LGD
    f["r2_per_step"] = f["r2_expected_loss"] * RECOVERY_STEP

    # R3: watch list — healthy live loans the model flags
    watch = loans[(loans.status == "running_ok") & (loans.flagged == 1)]
    finished_flagged = loans[loans.status.str.startswith("finished") & (loans.flagged == 1)]
    f |= {
        "r3_loans": len(watch),
        "r3_czk": watch.amount_czk.sum(),
        "r3_live_ok": int((loans.status == "running_ok").sum()),
        "r3_hist_rate": finished_flagged.is_default.mean(),
        "r3_hist_n": len(finished_flagged),
    }
    f["r3_scenario_loss"] = f["r3_czk"] * WATCHLIST_SCENARIO * LGD

    # Model and controls context
    scores = loans
    flagged = scores[scores.flagged == 1]
    f |= {
        "model_flag_rate": scores.flagged.mean(),
        "model_recall": flagged.is_default.sum() / d,
        "model_precision": flagged.is_default.mean(),
        "model_defaulted_czk_share": flagged.loc[flagged.is_default == 1, "amount_czk"].sum() / f["defaulted_czk"],
    }
    recon = reconcile.monthly_variance(conn)
    f["recon_ok"], f["recon_months"] = int((recon.status == "PASS").sum()), len(recon)
    f["transactions"] = int(recon.rows_model.sum())

    seg = pd.read_sql(
        """select s.segment, count(*) accounts, sum(l.is_default) defaults, count(l.loan_id) loans
           from rpt_account_segments s left join rpt_fact_loans l using (account_key) group by s.segment order by s.segment""",
        conn,
    )
    f["segments"] = seg
    return f


def memo(f: dict) -> str:
    seg = f["segments"].set_index("segment")
    s1, s3 = seg.loc[1], seg.loc[3]
    lines = [
        "# Findings memo: loan risk at a Czech retail bank, 1993–1998",
        "",
        f"*Generated {datetime.now(timezone.utc):%Y-%m-%d} by `python -m card_analytics.findings`. Every number is computed from the reporting layer.*",
        "",
        "**To:** Head of Retail Credit Risk · **From:** Mukesh Ethiraj, Analytics · **Data:** Berka (PKDD'99), "
        f"{f['transactions']:,} transactions, {f['loans']} loans",
        "",
        "## Summary",
        "",
        f"The loan book holds {f['loans']} loans worth {czk_m(f['book_czk'])}. {f['defaults']} defaulted ({f['default_rate']:.1%}), "
        f"{czk_m(f['defaulted_czk'])} of principal. Most of that loss was predictable **before the loan was granted**: a model using only",
        f"pre-loan account behaviour flags {f['model_defaulted_czk_share']:.0%} of defaulted principal. A simple rule, a collections queue and a",
        "watch list, all built from data the bank already has, put that to work.",
        "",
        "## Three recommendations",
        "",
        "### 1. Refer any applicant with a pre-loan overdraft or penalty interest",
        "",
        f"{f['r1_loans']} loans went to accounts that had been overdrawn or charged penalty interest before the loan started. "
        f"**{f['r1_defaults']} of them defaulted ({f['r1_rate']:.0%})**, against {f['r1_rest_rate']:.1%} for everyone else. "
        f"Together they account for {f['r1_share_of_defaulted']:.0%} of all defaulted principal.",
        "",
        f"- **Impact:** {czk_m(f['r1_defaulted_czk'])} of defaulted principal; at {LGD:.0%} loss given default, "
        f"**up to ≈ {czk_m(f['r1_loss_avoided'])} of losses the rule would have avoided**.",
        f"- **Cost:** {f['r1_good_loans']} good loans ({czk_m(f['r1_good_czk'])}) would also have been referred; "
        f"at a {NET_MARGIN:.0%} margin that is ≈ {czk_m(f['r1_margin_forgone'])} of interest income at risk, and only if they were declined rather than approved after review.",
        "- **Action:** add the rule to origination as a mandatory manual review. No model needed: it's two lookups on the account history.",
        "",
        "### 2. Work the arrears queue in model-score order",
        "",
        f"{f['r2_loans']} live loans ({czk_m(f['r2_czk'])}) are already running in debt. The default model ranks "
        f"{f['r2_flagged']} of them above its alert threshold, so collectors can start with the highest-risk accounts.",
        "",
        f"- **Impact:** expected loss on the queue is ≈ {czk_m(f['r2_expected_loss'])} at {LGD:.0%} LGD; "
        f"**every extra {RECOVERY_STEP:.0%} recovered is worth ≈ {czk_m(f['r2_per_step'])}**.",
        "- **Action:** feed `rpt_loan_scores` into the collections system each month and track recovery rate by score band.",
        "",
        "### 3. Put healthy loans that look like past defaulters on a watch list",
        "",
        f"{f['r3_loans']} of the {f['r3_live_ok']} loans that are currently up to date ({czk_m(f['r3_czk'])}) score above the threshold. "
        f"Among flagged loans that have already finished, {f['r3_hist_rate']:.0%} defaulted ({f['r3_hist_n']} loans).",
        "",
        f"- **Impact (scenario):** if {WATCHLIST_SCENARIO:.0%} of the watch list defaults, well below that historical rate, "
        f"expected loss is **≈ {czk_m(f['r3_scenario_loss'])}**. Early contact (payment plans, restructuring) is cheaper than collections.",
        "- **Action:** monthly watch-list report to relationship managers; measure how many move into arrears vs a control group.",
        "",
        "Some loans appear under more than one recommendation, so the three impacts overlap and shouldn't be added up.",
        "",
        "## Supporting findings",
        "",
        f"- **The model.** Logistic regression on pre-loan behaviour flags {f['model_flag_rate']:.0%} of loans and catches "
        f"{f['model_recall']:.0%} of defaults ({f['model_defaulted_czk_share']:.0%} of defaulted principal); "
        f"{f['model_precision']:.0%} of flags default vs {f['default_rate']:.0%} at random. See the [model card](model-card.md).",
        f"- **Segments.** Card holders (segment 1, {int(s1.accounts):,} accounts) default on {int(s1.defaults)} of {int(s1.loans)} loans; "
        f"the cash-only, low-activity segment 3 ({int(s3.accounts):,} accounts) on {int(s3.defaults)} of {int(s3.loans)}. "
        "Segments use the whole history, so they are for portfolio monitoring, not loan decisions. See [segmentation](segmentation.md).",
        f"- **Data you can trust.** {f['recon_ok']}/{f['recon_months']} months reconcile to the source to the cent, and the pipeline blocks any refresh that fails a control. "
        "See the [reconciliation report](reconciliation-report.md).",
        "",
        "## Assumptions and limits",
        "",
        "| Assumption | Value | Why |",
        "| --- | ---: | --- |",
        f"| Loss given default | {LGD:.0%} | Basel II foundation-IRB supervisory LGD for senior unsecured corporate claims, used as a benchmark |",
        f"| Net interest margin | {NET_MARGIN:.0%} a year | Illustrative; replace with the bank's own figure |",
        f"| Watch-list default scenario | {WATCHLIST_SCENARIO:.0%} | Illustrative, deliberately below the historical rate |",
        "",
        "Loss uses original loan amount as exposure (the data has no outstanding balances), so losses are upper bounds. "
        f"The sample is small ({f['loans']} loans, {f['defaults']} defaults), and loans that started in 1998 have had little time to default. "
        "Amounts are in Czech koruna (CZK), the bank's currency.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args(argv)
    conn = connect(args.db)
    tables = set(pd.read_sql("select name from sqlite_master where type = 'table'", conn).name) if args.db.startswith("sqlite") else None
    if tables is not None and not {"rpt_loan_scores", "rpt_account_segments"} <= tables:
        # Happens when the data is too small to segment or model (e.g. the test sample): nothing to write.
        print("Loan scores or segments are missing (run the pipeline on the full data first): memo skipped.")
        return 0
    f = compute(conn)
    args.docs.mkdir(parents=True, exist_ok=True)
    path = args.docs / "findings-memo.md"
    path.write_text(memo(f), encoding="utf-8")
    print(f"Memo written to {path}")
    print(f"  R1 rule: {f['r1_defaults']}/{f['r1_loans']} defaulted, ~{czk_m(f['r1_loss_avoided'])} losses avoided")
    print(f"  R2 arrears: {f['r2_loans']} loans, {czk_m(f['r2_czk'])}, {czk_m(f['r2_per_step'])} per extra 10% recovered")
    print(f"  R3 watch list: {f['r3_loans']} loans, {czk_m(f['r3_czk'])}, scenario loss {czk_m(f['r3_scenario_loss'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

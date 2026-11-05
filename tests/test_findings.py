"""Findings memo tests: the numbers come from the data, and the memo has three recommendations."""
import sqlite3

import pandas as pd

from card_analytics import berka, findings, mask, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def build(src, tmp_path):
    db = tmp_path / "f.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    mask.build(conn, "test-salt")
    key = pd.read_sql("select account_key from rpt_fact_loans", conn).account_key[0]
    pd.DataFrame({"loan_id": [5], "account_key": [key], "default_score": [0.9], "flagged": [1], "is_default": [1]}).to_sql(
        "rpt_loan_scores", conn, index=False)
    pd.DataFrame({"account_key": [key], "segment": [1]}).to_sql("rpt_account_segments", conn, index=False)
    return conn


def test_compute_counts_the_sample_loan(src, tmp_path):
    f = findings.compute(build(src, tmp_path))
    assert f["loans"] == 1 and f["defaults"] == 1
    assert f["defaulted_czk"] == 96396
    assert f["r1_loans"] == 0  # balances stayed positive before the loan: no warning sign
    assert f["model_recall"] == 1.0


def fake_facts():
    seg = pd.DataFrame({"segment": [1, 2, 3, 4], "accounts": [800, 1800, 500, 1200], "defaults": [5, 50, 19, 0], "loans": [165, 487, 27, 3]})
    keys = ["loans", "defaults", "default_rate", "book_czk", "defaulted_czk", "r1_loans", "r1_defaults", "r1_rate", "r1_rest_rate",
            "r1_defaulted_czk", "r1_good_loans", "r1_good_czk", "r1_margin_forgone", "r1_loss_avoided", "r1_share_of_defaulted",
            "r2_loans", "r2_czk", "r2_flagged", "r2_expected_loss", "r2_per_step", "r3_loans", "r3_czk", "r3_live_ok", "r3_hist_rate",
            "r3_hist_n", "r3_scenario_loss", "model_flag_rate", "model_recall", "model_precision", "model_defaulted_czk_share",
            "recon_ok", "recon_months", "transactions"]
    return {k: 1 for k in keys} | {"segments": seg}


def test_memo_has_three_recommendations_and_states_assumptions():
    text = findings.memo(fake_facts())
    assert text.count("\n### ") == 3
    assert "Loss given default" in text and "Net interest margin" in text
    assert "shouldn't be added up" in text


def test_memo_is_skipped_without_scores(src, tmp_path):
    db = tmp_path / "g.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    assert findings.main(["--db", f"sqlite:///{db}", "--docs", str(tmp_path)]) == 0
    assert not (tmp_path / "findings-memo.md").exists()

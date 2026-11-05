"""Loan default model tests: no look-ahead, model choice and threshold logic."""
import sqlite3

import numpy as np
import pandas as pd
from sklearn.datasets import make_classification

from card_analytics import berka, mask, model, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def build(src, tmp_path):
    db = tmp_path / "model.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    mask.build(conn, "test-salt")
    return conn


def test_features_only_use_transactions_before_the_loan(src, tmp_path):
    conn = build(src, tmp_path)
    before = model.build_features(conn)
    # The sample loan starts 1993-07-05. Add a huge overdraft AFTER that date: features must not change.
    row = pd.read_sql("select * from rpt_fact_transactions limit 1", conn)
    row = row.assign(trans_id=999, date_key=19930801, direction="debit", amount_czk=99999.0,
                     signed_amount_czk=-99999.0, balance_after_czk=-99000.0, is_overdrawn=1)
    row.to_sql("rpt_fact_transactions", conn, if_exists="append", index=False)
    after = model.build_features(conn)
    pd.testing.assert_frame_equal(before, after)
    assert before.months_history.iloc[0] == 1 and before.min_balance.iloc[0] == 500


def test_threshold_meets_target_recall():
    rng = np.random.default_rng(0)
    y = (rng.random(500) < 0.1).astype(int)
    p = np.clip(y * 0.4 + rng.random(500) * 0.6, 0, 1)
    t = model.choose_threshold(y, p, target_recall=0.75)
    assert t["recall"] >= 0.75
    assert t["tp"] + t["fn"] == y.sum() and t["tp"] + t["fp"] + t["fn"] + t["tn"] == len(y)


def test_simpler_model_wins_a_near_tie():
    scores = pd.DataFrame({"model": ["Baseline", "Logistic regression", "Gradient boosting"], "pr_auc": [0.11, 0.685, 0.694]})
    assert model.pick_model(scores) == "Logistic regression"
    scores.loc[2, "pr_auc"] = 0.80
    assert model.pick_model(scores) == "Gradient boosting"


def test_real_models_beat_the_baseline():
    X, y = make_classification(n_samples=600, n_features=8, n_informative=4, weights=[0.88], random_state=0)
    scores = model.compare(X, y, n_repeats=1).set_index("model")
    baseline = scores.iloc[0].pr_auc
    assert abs(baseline - y.mean()) < 0.03
    assert (scores.iloc[1:].pr_auc > baseline + 0.2).all()


def test_too_few_loans_is_skipped(src, tmp_path):
    conn = build(src, tmp_path)
    conn.close()
    assert model.main(["--db", f"sqlite:///{tmp_path / 'model.db'}", "--docs", str(tmp_path)]) == 0
    assert not (tmp_path / "model-card.md").exists()

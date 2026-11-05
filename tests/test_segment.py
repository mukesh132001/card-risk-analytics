"""Segmentation tests: k selection, features and profiles."""
import sqlite3

import numpy as np
import pandas as pd
from sklearn.datasets import make_blobs

from card_analytics import berka, mask, segment, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def test_choose_k_finds_the_true_number_of_groups():
    X, _ = make_blobs(n_samples=400, centers=4, n_features=5, cluster_std=0.6, random_state=0)
    k, scores = segment.choose_k(X)
    assert k == 4
    assert list(scores.k) == list(segment.K_RANGE)


def test_features_from_the_reporting_layer(src, tmp_path):
    db = tmp_path / "seg.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    mask.build(conn, "test-salt")
    feats = segment.build_features(conn)
    row = feats.iloc[0]
    assert len(feats) == 1  # only account 1 has transactions in the sample
    assert row.monthly_in == 700 and row.monthly_out == 200
    assert row.cash_share == 1.0 and row.has_card == 1


def synthetic_features(n=300, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({c: rng.normal(size=n) for c, _ in segment.FEATURES})
    df["has_card"] = (rng.random(n) > 0.7).astype(int)
    df["account_key"] = [f"A-{i:012x}" for i in range(n)]
    df["monthly_in"] = rng.lognormal(9, 1, n)
    df["monthly_out"] = df.monthly_in * 0.9
    df["avg_balance"] = rng.lognormal(10, 0.5, n)
    df["txns_per_month"] = rng.uniform(2, 8, n)
    df["has_loan"] = (rng.random(n) > 0.8).astype(int)
    df["loan_is_default"] = df.has_loan * (rng.random(n) > 0.85)
    return df


def test_segments_are_numbered_by_money_in():
    feats = synthetic_features()
    labels, k, _, X = segment.fit(feats, k=3)
    means = feats.assign(segment=labels).groupby("segment").monthly_in.mean()
    assert list(means.index) == [1, 2, 3]
    assert means.is_monotonic_decreasing


def test_profile_counts_add_up():
    feats = synthetic_features()
    labels, k, _, X = segment.fit(feats, k=3)
    prof, desc = segment.profile(feats, labels, X)
    assert prof.accounts.sum() == len(feats)
    assert abs(prof.share_of_accounts.sum() - 1) < 1e-9
    assert set(desc) == {1, 2, 3}


def test_too_few_accounts_is_skipped_not_crashed(src, tmp_path):
    db = tmp_path / "seg.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    mask.build(conn, "test-salt")
    assert segment.run(conn).empty

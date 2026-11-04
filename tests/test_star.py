"""Star-schema tests on the tiny Berka sample from test_berka."""
import sqlite3

from card_analytics import berka, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def build(src, tmp_path):
    db = tmp_path / "star.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    return conn


def test_star_builds_and_all_model_tests_pass(src, tmp_path):
    conn = build(src, tmp_path)
    results = star.test_models(conn)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_dim_account_rollups(src, tmp_path):
    conn = build(src, tmp_path)
    row = conn.execute(
        "select owner_client_id, n_cards, card_type, has_loan, loan_is_default from dim_account where account_id = 1"
    ).fetchone()
    assert row == (1, 1, "classic", 1, 1)


def test_fact_date_key_format(src, tmp_path):
    conn = build(src, tmp_path)
    keys = [r[0] for r in conn.execute("select date_key from fact_transactions order by trans_id")]
    assert keys == [19930101, 19930105]

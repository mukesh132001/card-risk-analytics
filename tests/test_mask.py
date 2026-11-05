"""Reporting-layer masking and data-dictionary tests on the tiny Berka sample."""
import sqlite3

import pandas as pd

from card_analytics import berka, catalog, mask, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def build(src, tmp_path, salt="test-salt"):
    db = tmp_path / "rpt.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    mask.build(conn, salt)
    return conn


def test_all_masking_checks_pass(src, tmp_path):
    conn = build(src, tmp_path)
    results = mask.check(conn)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_no_raw_ids_or_birth_dates_in_reporting_layer(src, tmp_path):
    conn = build(src, tmp_path)
    client = pd.read_sql("select * from rpt_dim_client", conn)
    assert "client_id" not in client.columns and "birth_date" not in client.columns
    assert client.client_key.str.match(r"^C-[0-9a-f]{12}$").all()


def test_tokens_are_consistent_across_tables(src, tmp_path):
    conn = build(src, tmp_path)
    fact = set(pd.read_sql("select client_key from rpt_fact_transactions", conn).client_key)
    dim = set(pd.read_sql("select client_key from rpt_dim_client", conn).client_key)
    assert fact <= dim


def test_salt_changes_tokens():
    assert mask.token(1, "C", "salt-a") == mask.token(1, "C", "salt-a")
    assert mask.token(1, "C", "salt-a") != mask.token(1, "C", "salt-b")


def test_leaked_column_is_caught(src, tmp_path):
    conn = build(src, tmp_path)
    conn.execute("alter table rpt_dim_client add column birth_date text")
    failed = {r.name for r in mask.check(conn) if not r.passed}
    assert "rpt_dim_client: no raw identifiers" in failed


def test_dictionary_matches_database(src, tmp_path):
    conn = build(src, tmp_path)
    assert catalog.drift(conn) == []


def test_undocumented_column_is_caught(src, tmp_path):
    conn = build(src, tmp_path)
    conn.execute("alter table fact_loans add column mystery integer")
    assert catalog.drift(conn) == ["fact_loans.mystery: in database, not documented"]


def test_lineage_comes_from_model_sql():
    edges = catalog.lineage_edges()
    assert ("berka_trans", "fact_transactions") in edges
    assert ("dim_account", "fact_transactions") in edges
    assert ("fact_transactions", "rpt_fact_transactions") in edges

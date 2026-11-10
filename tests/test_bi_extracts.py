"""Power BI extract tests on the tiny Berka sample."""
import sqlite3

import pandas as pd
import pytest

from card_analytics import berka, bi_extracts, mask, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


@pytest.fixture
def conn(src, tmp_path):
    db = tmp_path / "bi.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    c = sqlite3.connect(db)
    star.build(c)
    mask.build(c, "test-salt")
    return c


def test_extracts_reconcile_to_the_reporting_layer(conn):
    t = bi_extracts.build(conn)
    spend = conn.execute("select sum(amount_czk) from rpt_fact_transactions where direction='debit'").fetchone()[0]
    assert t["region_month"].spend_czk.sum() == pytest.approx(spend)
    assert t["loan_book"].loans.sum() == conn.execute("select count(*) from rpt_fact_loans").fetchone()[0]
    assert t["loan_book"].defaulted_loans.sum() == conn.execute("select sum(is_default) from rpt_fact_loans").fetchone()[0]


def test_delinquent_loans_are_a_subset_of_defaulted(conn):
    lb = bi_extracts.build(conn)["loan_book"]
    assert (lb.delinquent_loans <= lb.defaulted_loans).all()


def test_no_pii_columns_and_files_written(conn, tmp_path):
    paths = bi_extracts.write(conn, tmp_path / "x")
    assert set(paths) == {"region_month", "loan_book", "regions"}
    for p in paths.values():
        assert not set(pd.read_csv(p).columns) & mask.BANNED_COLUMNS

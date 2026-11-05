"""Reconciliation and data-quality gate tests on the tiny Berka sample."""
import sqlite3

from card_analytics import berka, pipeline, reconcile, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


def build(src, tmp_path):
    db = tmp_path / "recon.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    conn = sqlite3.connect(db)
    star.build(conn)
    return conn


def test_clean_build_reconciles(src, tmp_path):
    conn = build(src, tmp_path)
    monthly = reconcile.monthly_variance(conn)
    assert (monthly.status == "PASS").all()
    assert reconcile.run(conn)


def test_one_cent_difference_fails_the_right_month(src, tmp_path):
    conn = build(src, tmp_path)
    conn.execute("update fact_transactions set amount_czk = amount_czk + 0.01 where trans_id = 101")
    conn.commit()
    monthly = reconcile.monthly_variance(conn).set_index("year_month")
    assert monthly.loc["1993-01", "status"] == "FAIL"
    assert monthly.loc["1993-01", "debit_cents_var"] == -1
    assert not reconcile.run(conn)


def test_dropped_row_fails(src, tmp_path):
    conn = build(src, tmp_path)
    conn.execute("delete from fact_transactions where trans_id = 100")
    conn.commit()
    monthly = reconcile.monthly_variance(conn).set_index("year_month")
    assert monthly.loc["1993-01", "rows_var"] == 1
    assert not reconcile.run(conn)


def test_missing_loan_fails(src, tmp_path):
    conn = build(src, tmp_path)
    conn.execute("delete from fact_loans")
    conn.commit()
    assert not reconcile.loan_variance(conn).status.eq("PASS").all()


def test_report_is_written(src, tmp_path):
    conn = build(src, tmp_path)
    report = tmp_path / "report.md"
    reconcile.run(conn, report)
    text = report.read_text()
    assert "**Overall: PASS.**" in text
    assert "| 1993-01 |" in text


def test_gate_stops_at_first_failure():
    ran = []
    steps = [
        ("ok", lambda: ran.append("ok") or 0),
        ("bad", lambda: ran.append("bad") or 1),
        ("never", lambda: ran.append("never") or 0),
    ]
    assert pipeline.run_steps(steps) == 1
    assert ran == ["ok", "bad"]


def test_full_pipeline_passes(src, tmp_path):
    db = tmp_path / "pipe.db"
    report = tmp_path / "report.md"
    code = pipeline.main(["--db", f"sqlite:///{db}", "--src", str(src), "--report", str(report), "--docs", str(tmp_path)])
    assert code == 0
    assert report.exists()
    assert (tmp_path / "data-dictionary.md").exists() and (tmp_path / "lineage.md").exists()

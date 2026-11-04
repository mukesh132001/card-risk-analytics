from card_analytics.generate import generate
from card_analytics.quality import reconcile, run_checks


def test_generated_data_passes_all_checks():
    df = generate(rows=1000)
    results = run_checks(df)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_duplicate_ids_are_caught():
    df = generate(rows=100)
    df.loc[1, "transaction_id"] = df.loc[0, "transaction_id"]
    failed = {r.name for r in run_checks(df) if not r.passed}
    assert "unique_transaction_id" in failed


def test_reconciliation_detects_dropped_rows():
    source = generate(rows=500)
    model = source.iloc[:-1]
    assert reconcile(source, source).passed
    assert not reconcile(source, model).passed

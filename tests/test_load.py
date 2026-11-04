from card_analytics.generate import generate
from card_analytics.load import connect, load_csv, main, read_table


def test_load_roundtrip_sqlite(tmp_path):
    csv = tmp_path / "tx.csv"
    generate(rows=300).to_csv(csv, index=False)
    conn = connect(f"sqlite:///{tmp_path / 'test.db'}")
    source = load_csv(csv, conn, "raw_transactions")
    loaded = read_table(conn, "raw_transactions")
    assert len(loaded) == len(source) == 300


def test_cli_passes_checks(tmp_path, capsys):
    csv = tmp_path / "tx.csv"
    generate(rows=200).to_csv(csv, index=False)
    code = main(["--csv", str(csv), "--db", f"sqlite:///{tmp_path / 'cli.db'}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "6/6 checks passed" in out


def test_cli_fails_on_bad_data(tmp_path):
    csv = tmp_path / "bad.csv"
    df = generate(rows=50)
    df.loc[3, "amount_usd"] = -10
    df.to_csv(csv, index=False)
    assert main(["--csv", str(csv), "--db", f"sqlite:///{tmp_path / 'bad.db'}"]) == 1

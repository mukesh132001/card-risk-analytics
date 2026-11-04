"""Berka loader tests on a tiny hand-made sample (the real data is git-ignored)."""
from pathlib import Path

import pytest

from card_analytics.berka import check_all, clean_all, main, _read, TABLES

SAMPLE = {
    "district": 'A1;A2;A3;A4;A5;A6;A7;A8;A9;A10;A11;A12;A13;A14;A15;A16\n'
                '1;"Hl.m. Praha";"Prague";1204953;0;0;0;1;1;100.0;12541;0.29;0.43;167;85677;99107\n'
                '2;"Benesov";"central Bohemia";88884;80;26;6;2;5;46.7;8507;?;1.85;132;?;2674\n',
    "account": '"account_id";"district_id";"frequency";"date"\n1;1;"POPLATEK MESICNE";930101\n2;2;"POPLATEK TYDNE";930102\n',
    "client": '"client_id";"birth_number";"district_id"\n1;"706213";1\n2;"450204";2\n',
    "disp": '"disp_id";"client_id";"account_id";"type"\n1;1;1;"OWNER"\n2;2;2;"OWNER"\n3;2;1;"DISPONENT"\n',
    "card": '"card_id";"disp_id";"type";"issued"\n10;1;"classic";931107 00:00:00\n',
    "loan": '"loan_id";"account_id";"date";"amount";"duration";"payments";"status"\n5;1;930705;96396;12;8033.00;"B"\n',
    "order": '"order_id";"account_id";"bank_to";"account_to";"amount";"k_symbol"\n7;1;"YZ";"87144583";2452.00;"SIPO"\n',
    "trans": '"trans_id";"account_id";"date";"type";"operation";"amount";"balance";"k_symbol";"bank";"account"\n'
             '100;1;930101;"PRIJEM";"VKLAD";700.00;700.00;"";;\n'
             '101;1;930105;"VYDAJ";"VYBER";200.00;500.00;"SLUZBY";;\n',
}


@pytest.fixture
def src(tmp_path: Path) -> Path:
    for name, text in SAMPLE.items():
        (tmp_path / f"{name}.csv").write_text(text)
    return tmp_path


def test_translations_and_derived_fields(src):
    t = clean_all(src)
    assert list(t["account"]["frequency"]) == ["monthly", "weekly"]
    assert list(t["client"]["gender"]) == ["F", "M"]          # 706213: month 62 = 12 + 50 -> female, born Dec 13 1970
    assert str(t["client"]["birth_date"][0].date()) == "1970-12-13"
    assert t["loan"]["is_default"].iloc[0] == 1               # status B = finished, defaulted
    trans = t["trans"]
    assert list(trans["direction"]) == ["credit", "debit"]
    assert list(trans["signed_amount_czk"]) == [700.0, -200.0]
    assert trans["k_symbol"].iloc[1] == "statement_fee"
    assert t["district"]["crimes_95"].isna().sum() == 1       # '?' -> missing


def test_all_integrity_checks_pass(src):
    raw = {name: len(_read(src, name)) for name in TABLES}
    results = check_all(raw, clean_all(src))
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_orphan_transaction_is_caught(src):
    (src / "trans.csv").write_text(SAMPLE["trans"] + '102;99;930106;"PRIJEM";"VKLAD";50.00;50.00;"";;\n')
    raw = {name: len(_read(src, name)) for name in TABLES}
    failed = {r.name for r in check_all(raw, clean_all(src)) if not r.passed}
    assert "trans.account_id -> account" in failed


def test_cli_loads_sqlite(src, tmp_path):
    assert main(["--src", str(src), "--db", f"sqlite:///{tmp_path / 'b.db'}"]) == 0

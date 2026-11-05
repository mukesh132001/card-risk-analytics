"""Excel MI pack tests on the tiny Berka sample."""
import sqlite3

import pytest
from openpyxl import load_workbook

from card_analytics import berka, mask, mipack, star
from test_berka import SAMPLE, src  # noqa: F401  (reuse the sample fixture)


@pytest.fixture
def conn(src, tmp_path):
    db = tmp_path / "mi.db"
    assert berka.main(["--src", str(src), "--db", f"sqlite:///{db}"]) == 0
    c = sqlite3.connect(db)
    star.build(c)
    mask.build(c, "test-salt")
    return c


def test_pack_has_the_expected_tabs(conn, tmp_path):
    wb = load_workbook(mipack.build_pack(conn, tmp_path / "pack.xlsx"))
    assert wb.sheetnames == ["Summary", "Pivot", "Notes", "Monthly", "Region_Month", "Loans", "Accounts"]


def test_summary_defaults_to_latest_month_and_uses_formulas(conn, tmp_path):
    ws = load_workbook(mipack.build_pack(conn, tmp_path / "pack.xlsx"))["Summary"]
    assert ws["C3"].value == "1993-01"
    assert ws["B8"].value.startswith("=SUMIFS(Monthly!")
    assert ws["C4"].value.startswith("=IF(MATCH(")


def test_data_tab_matches_the_reporting_layer(conn, tmp_path):
    ws = load_workbook(mipack.build_pack(conn, tmp_path / "pack.xlsx"))["Monthly"]
    header = [c.value for c in ws[1]]
    row = dict(zip(header, [c.value for c in ws[2]]))
    assert row["year_month"] == "1993-01"
    assert row["money_in_czk"] == 700.0 and row["money_out_czk"] == 200.0 and row["transactions"] == 2


def test_no_raw_identifiers_in_any_tab(conn, tmp_path):
    wb = load_workbook(mipack.build_pack(conn, tmp_path / "pack.xlsx"))
    for name in ["Monthly", "Region_Month", "Loans", "Accounts"]:
        header = {c.value for c in wb[name][1]}
        assert not header & mask.BANNED_COLUMNS, name


def test_unknown_month_is_rejected(conn, tmp_path):
    with pytest.raises(ValueError, match="not in data"):
        mipack.build_pack(conn, tmp_path / "pack.xlsx", month="2001-01")

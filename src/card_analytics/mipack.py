"""Monthly MI pack: an Excel workbook generated from the PII-masked reporting layer.

Usage:
    python -m card_analytics.mipack --db sqlite:///cardrisk.db
    python -m card_analytics.mipack --db sqlite:///cardrisk.db --month 1997-06 --out dashboards/mi_pack.xlsx

Tabs:
    Summary       the one-page pack: pick a month in C3 and every number updates
                  (KPIs vs prior month and same month last year, region table, loan book)
    Pivot         money in by region x year (CZK m), a SUMIFS cross-tab
    Monthly, Region_Month, Loans, Accounts   data tabs the formulas read from
    Notes         sources, definitions and the control results at generation time

Every number on Summary and Pivot is an Excel formula (SUMIFS / COUNTIFS / INDEX-MATCH)
over the data tabs, so the pack recalculates when the month or the data changes.
Only rpt_ tables, dim_date and dim_district are read: no raw client or account IDs.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from card_analytics import reconcile
from card_analytics.load import DEFAULT_DB, connect

DEFAULT_OUT = Path("dashboards/mi_pack.xlsx")

MONTHLY_SQL = """
select dt.year_month,
       sum(case when f.direction = 'credit' then f.amount_czk else 0 end) as money_in_czk,
       sum(case when f.direction = 'debit'  then f.amount_czk else 0 end) as money_out_czk,
       count(*)                                                          as transactions,
       count(distinct f.account_key)                                     as active_accounts,
       sum(f.is_overdrawn)                                               as overdrawn_txns
from rpt_fact_transactions f
join dim_date dt on dt.date_key = f.date_key
group by dt.year_month
order by dt.year_month
"""

REGION_MONTH_SQL = """
select dt.year_month,
       d.region,
       cast(dt.year as text)                                             as year,
       sum(case when f.direction = 'credit' then f.amount_czk else 0 end) as money_in_czk,
       sum(case when f.direction = 'debit'  then f.amount_czk else 0 end) as money_out_czk,
       count(*)                                                          as transactions,
       count(distinct f.account_key)                                     as active_accounts
from rpt_fact_transactions f
join dim_date dt     on dt.date_key     = f.date_key
join dim_district d  on d.district_id   = f.district_id
group by dt.year_month, d.region, dt.year
order by dt.year_month, d.region
"""

LOANS_SQL = """
select l.loan_id, d.region, d.district_name, l.status, l.is_default, l.amount_czk, dt.year_month as start_month
from rpt_fact_loans l
join dim_district d on d.district_id = l.district_id
join dim_date dt    on dt.date_key   = l.date_key
order by l.loan_id
"""

ACCOUNTS_SQL = """
select a.account_key, d.region, a.n_cards, coalesce(a.card_type, 'none') as card_type, a.has_loan
from rpt_dim_account a
join dim_district d on d.district_id = a.district_id
order by d.region, a.account_key
"""

# ---- styling ----
FONT = "Arial"
NAVY = "1F3864"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=16, bold=True, color=NAVY)
F_SUB = Font(name=FONT, size=10, italic=True, color="595959")
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_INPUT = Font(name=FONT, size=11, bold=True, color="0000FF")
F_LINK = Font(name=FONT, size=10, color="008000")
FILL_HEAD = PatternFill("solid", start_color=NAVY)
FILL_INPUT = PatternFill("solid", start_color="FFFF00")
FILL_TOTAL = PatternFill("solid", start_color="D9E1F2")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)

CZK = '#,##0;(#,##0);"-"'
CZK_M = '#,##0.0;(#,##0.0);"-"'
COUNT = '#,##0;(#,##0);"-"'
PCT = '0.0%;(0.0%);"-"'


def head(ws, row: int, labels: list[str], col: int = 1) -> None:
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=label)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def body(cell, fmt: str | None = None, font: Font = F_BASE) -> None:
    cell.font, cell.border = font, BOX
    if fmt:
        cell.number_format = fmt


def fit_to_page(ws) -> None:
    """Print landscape, one page wide."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0


def data_tab(wb: Workbook, name: str, df: pd.DataFrame, formats: dict[str, str]) -> None:
    ws = wb.create_sheet(name)
    head(ws, 1, list(df.columns))
    for r, row in enumerate(df.itertuples(index=False), start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=None if pd.isna(value) else (value.item() if hasattr(value, "item") else value))
            cell.font = F_BASE
            fmt = formats.get(df.columns[c - 1])
            if fmt:
                cell.number_format = fmt
    for c, col in enumerate(df.columns, start=1):
        ws.column_dimensions[get_column_letter(c)].width = max(12, len(col) + 2)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def build_summary(wb: Workbook, month: str, regions: list[str], n_months: int) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    for col, width in zip("ABCDEFGHIJ", [30, 16, 16, 16, 16, 14, 14, 12, 12, 14]):
        ws.column_dimensions[col].width = width

    ws["A1"] = "Card Risk Analytics: monthly MI pack"
    ws["A1"].font = F_TITLE
    ws["A2"] = "Berka bank data, PII-masked reporting layer. Change the yellow cell to report another month."
    ws["A2"].font = F_SUB

    ws["A3"], ws["A4"], ws["A5"] = "Reporting month", "Prior month", "Same month last year"
    for a in ("A3", "A4", "A5"):
        ws[a].font = F_BOLD
    ws["C3"] = month
    ws["C3"].font, ws["C3"].fill, ws["C3"].border = F_INPUT, FILL_INPUT, BOX
    ws["C3"].alignment = Alignment(horizontal="center")
    ws["C3"].comment = Comment("Input: pick a month (YYYY-MM) from the list. Every number on this tab updates.", "MI pack")
    last = n_months + 1
    dv = DataValidation(type="list", formula1=f"=Monthly!$A$2:$A${last}", allow_blank=False)
    dv.error, dv.errorTitle = "Pick a month from the list (YYYY-MM).", "Unknown month"
    ws.add_data_validation(dv)
    dv.add("C3")
    ws["C4"] = '=IF(MATCH($C$3,Monthly!$A:$A,0)<=2,"n/a",INDEX(Monthly!$A:$A,MATCH($C$3,Monthly!$A:$A,0)-1))'
    ws["C5"] = '=(VALUE(LEFT($C$3,4))-1)&RIGHT($C$3,3)'
    for a in ("C4", "C5"):
        ws[a].font, ws[a].alignment = F_BASE, Alignment(horizontal="center")

    # ---- KPI block ----
    r0 = 7
    ws.cell(row=r0 - 1, column=1, value="Key indicators").font = F_BOLD
    head(ws, r0, ["Metric", "Reporting month", "Prior month", "Change", "Same month last year", "Change YoY"])
    kpis = [
        ("Money in (CZK)", "B", CZK),
        ("Money out (CZK)", "C", CZK),
        ("Net flow (CZK)", None, CZK),
        ("Transactions", "D", COUNT),
        ("Active accounts", "E", COUNT),
        ("Overdrawn transactions", "F", COUNT),
        ("Overdrawn rate", None, PCT),
    ]
    rows = {}
    for i, (label, col, fmt) in enumerate(kpis, start=r0 + 1):
        rows[label] = i
        body(ws.cell(row=i, column=1, value=label), font=F_BOLD)
        for c, ref in ((2, "$C$3"), (3, "$C$4"), (5, "$C$5")):
            L = get_column_letter(c)
            if label == "Net flow (CZK)":
                f = f"={L}{rows['Money in (CZK)']}-{L}{rows['Money out (CZK)']}"
            elif label == "Overdrawn rate":
                f = f"=IF({L}{rows['Transactions']}=0,0,{L}{rows['Overdrawn transactions']}/{L}{rows['Transactions']})"
            else:
                f = f"=SUMIFS(Monthly!${col}:${col},Monthly!$A:$A,{ref})"
            body(ws.cell(row=i, column=c, value=f), fmt)
        # Change columns: relative change, or percentage-point change for the rate.
        for c, base in ((4, "C"), (6, "E")):
            if label == "Overdrawn rate":
                f = f"=B{i}-{base}{i}"
                fmt_c = '+0.00%;-0.00%;"-"'
            else:
                f = f'=IF({base}{i}=0,0,B{i}/{base}{i}-1)'
                fmt_c = '+0.0%;-0.0%;"-"'
            body(ws.cell(row=i, column=c, value=f), fmt_c)
    ws.cell(row=rows["Overdrawn rate"], column=4).comment = Comment("Percentage-point change, not relative change.", "MI pack")

    # ---- Region table ----
    r1 = r0 + len(kpis) + 3
    ws.cell(row=r1 - 1, column=1, value="By region, reporting month").font = F_BOLD
    head(
        ws,
        r1,
        ["Region", "Money in (CZK)", "Money out (CZK)", "Net flow (CZK)", "Transactions", "Active accounts",
         "Share of money in", "Loans (all time)", "Default rate", "Accounts with a card"],
    )
    first = r1 + 1
    for i, region in enumerate(regions, start=first):
        body(ws.cell(row=i, column=1, value=region), font=F_BOLD)
        crit = f"Region_Month!$A:$A,$C$3,Region_Month!$B:$B,$A{i}"
        body(ws.cell(row=i, column=2, value=f"=SUMIFS(Region_Month!$D:$D,{crit})"), CZK)
        body(ws.cell(row=i, column=3, value=f"=SUMIFS(Region_Month!$E:$E,{crit})"), CZK)
        body(ws.cell(row=i, column=4, value=f"=B{i}-C{i}"), CZK)
        body(ws.cell(row=i, column=5, value=f"=SUMIFS(Region_Month!$F:$F,{crit})"), COUNT)
        body(ws.cell(row=i, column=6, value=f"=SUMIFS(Region_Month!$G:$G,{crit})"), COUNT)
        last_region = first + len(regions) - 1
        body(ws.cell(row=i, column=7, value=f"=IF(SUM($B${first}:$B${last_region})=0,0,B{i}/SUM($B${first}:$B${last_region}))"), PCT)
        body(ws.cell(row=i, column=8, value=f"=COUNTIFS(Loans!$B:$B,$A{i})"), COUNT)
        body(ws.cell(row=i, column=9, value=f"=IF(H{i}=0,0,COUNTIFS(Loans!$B:$B,$A{i},Loans!$E:$E,1)/H{i})"), PCT)
        body(ws.cell(row=i, column=10, value=f'=COUNTIFS(Accounts!$B:$B,$A{i},Accounts!$C:$C,">0")'), COUNT)
    tot = first + len(regions)
    body(ws.cell(row=tot, column=1, value="Total"), font=F_BOLD)
    for c in range(2, 11):
        L = get_column_letter(c)
        if c == 7:
            f = f"=SUM({L}{first}:{L}{tot - 1})"
            fmt = PCT
        elif c == 9:
            f = f"=IF(H{tot}=0,0,COUNTIFS(Loans!$E:$E,1)/H{tot})"
            fmt = PCT
        else:
            f = f"=SUM({L}{first}:{L}{tot - 1})"
            fmt = COUNT if c in (5, 6, 8, 10) else CZK
        body(ws.cell(row=tot, column=c, value=f), fmt, F_BOLD)
    for c in range(1, 11):
        ws.cell(row=tot, column=c).fill = FILL_TOTAL
    ws.cell(row=tot, column=6).comment = Comment("Sum of regions. Each account belongs to one district, so this equals the bank-wide count.", "MI pack")

    # ---- Check line: region total must tie back to the KPI block ----
    chk = tot + 1
    ws.cell(row=chk, column=1, value="Check: region total = key indicators").font = F_SUB
    ws.cell(row=chk, column=2, value=f'=IF(AND(ROUND(B{tot}-B{rows["Money in (CZK)"]},2)=0,ROUND(C{tot}-B{rows["Money out (CZK)"]},2)=0,E{tot}=B{rows["Transactions"]}),"OK","CHECK")')
    ws.cell(row=chk, column=2).font = F_BOLD

    # ---- Trend chart ----
    chart = LineChart()
    chart.title = "Money in vs money out by month (CZK)"
    chart.height, chart.width = 6.5, 22
    chart.y_axis.title, chart.x_axis.title = "CZK", "Month"
    chart.y_axis.numFmt = "#,##0"
    monthly = wb["Monthly"]
    chart.add_data(Reference(monthly, min_col=2, max_col=3, min_row=1, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(monthly, min_col=1, min_row=2, max_row=last))
    for series, name in zip(chart.series, ["Money in", "Money out"]):
        series.smooth = False
        series.tx = SeriesLabel(v=name)
    ws.add_chart(chart, f"A{chk + 3}")
    ws.freeze_panes = "A6"
    fit_to_page(ws)


def build_pivot(wb: Workbook, regions: list[str], years: list[str]) -> None:
    ws = wb.create_sheet("Pivot", 1)
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Money in by region and year (CZK m)"
    ws["A1"].font = F_TITLE
    ws["A2"] = "SUMIFS cross-tab over Region_Month. Add a year or region and the formulas extend the same way."
    ws["A2"].font = F_SUB
    head(ws, 4, ["Region"] + years + ["Total"])
    ws.column_dimensions["A"].width = 20
    for i, region in enumerate(regions, start=5):
        body(ws.cell(row=i, column=1, value=region), font=F_BOLD)
        for j, _ in enumerate(years, start=2):
            L = get_column_letter(j)
            f = f"=SUMIFS(Region_Month!$D:$D,Region_Month!$B:$B,$A{i},Region_Month!$C:$C,{L}$4)/1000000"
            body(ws.cell(row=i, column=j, value=f), CZK_M)
            ws.column_dimensions[L].width = 11
        last_year = get_column_letter(len(years) + 1)
        body(ws.cell(row=i, column=len(years) + 2, value=f"=SUM(B{i}:{last_year}{i})"), CZK_M, F_BOLD)
    tot = 5 + len(regions)
    body(ws.cell(row=tot, column=1, value="Total"), font=F_BOLD)
    for j in range(2, len(years) + 3):
        L = get_column_letter(j)
        body(ws.cell(row=tot, column=j, value=f"=SUM({L}5:{L}{tot - 1})"), CZK_M, F_BOLD)
    for j in range(1, len(years) + 3):
        ws.cell(row=tot, column=j).fill = FILL_TOTAL
    ws.column_dimensions[get_column_letter(len(years) + 2)].width = 12
    fit_to_page(ws)


def build_notes(wb: Workbook, db_label: str, months_ok: int, months: int, loans_ok: bool) -> None:
    ws = wb.create_sheet("Notes")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100
    rows = [
        ("Generated", f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by python -m card_analytics.mipack"),
        ("Database", db_label),
        ("Source tables", "rpt_fact_transactions, rpt_fact_loans, rpt_dim_account (PII masked), dim_date, dim_district"),
        ("Reconciliation at generation", f"{months_ok}/{months} months match source to the cent; loans {'PASS' if loans_ok else 'FAIL'}"),
        ("", ""),
        ("Definitions", ""),
        ("Money in / out", "Sum of credit / debit transaction amounts in the month, CZK"),
        ("Active accounts", "Accounts with at least one transaction in the month"),
        ("Overdrawn rate", "Transactions leaving a negative balance / all transactions"),
        ("Loans, default rate", "All loans by region regardless of month; default = finished_defaulted or running_in_debt"),
        ("Accounts with a card", "Accounts holding at least one card (junior, classic or gold) at the end of the data"),
        ("", ""),
        ("Cell colours", "Yellow fill + blue text = input you can change (Summary!C3). Black = formula."),
        ("Data tabs", "Monthly, Region_Month, Loans, Accounts: values written by Python from the reporting layer. Don't edit."),
    ]
    for r, (k, v) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=k).font = F_BOLD
        ws.cell(row=r, column=2, value=v).font = F_BASE
    ws.cell(row=4, column=2).font = F_LINK if (months_ok == months and loans_ok) else Font(name=FONT, size=10, color="FF0000")


def build_pack(conn, out: Path, month: str | None = None, db_label: str = "") -> Path:
    monthly = pd.read_sql(MONTHLY_SQL, conn)
    region_month = pd.read_sql(REGION_MONTH_SQL, conn)
    loans = pd.read_sql(LOANS_SQL, conn)
    accounts = pd.read_sql(ACCOUNTS_SQL, conn)
    if monthly.empty:
        raise ValueError("rpt_fact_transactions is empty: run python -m card_analytics.mask first")
    month = month or monthly.year_month.iloc[-1]
    if month not in set(monthly.year_month):
        raise ValueError(f"month {month} not in data ({monthly.year_month.iloc[0]} to {monthly.year_month.iloc[-1]})")
    regions = sorted(region_month.region.unique())
    years = sorted(region_month.year.unique())

    recon = reconcile.monthly_variance(conn)
    loan_checks = reconcile.loan_variance(conn)

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True  # Excel computes every formula when the file opens
    money = {"money_in_czk": CZK, "money_out_czk": CZK, "amount_czk": CZK}
    data_tab(wb, "Monthly", monthly, money)
    data_tab(wb, "Region_Month", region_month, money)
    data_tab(wb, "Loans", loans, money)
    data_tab(wb, "Accounts", accounts, {})
    build_summary(wb, month, regions, len(monthly))
    build_pivot(wb, regions, years)
    build_notes(wb, db_label, int((recon.status == "PASS").sum()), len(recon), bool((loan_checks.status == "PASS").all()))
    wb.move_sheet("Notes", offset=-(len(wb.sheetnames) - 3))
    for ws in wb.worksheets:
        ws.sheet_properties.tabColor = NAVY if ws.title in ("Summary", "Pivot") else "A6A6A6"

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--month", help="reporting month YYYY-MM (default: latest)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    label = args.db.split("@")[-1]  # never print a password from a connection string
    path = build_pack(connect(args.db), args.out, args.month, label)
    print(f"MI pack written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

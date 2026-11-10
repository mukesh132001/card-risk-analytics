"""CSV extracts for the Power BI dashboard, built from the PII-masked rpt_* tables.

Usage:
    python -m card_analytics.bi_extracts --db sqlite:///cardrisk.db

Writes dashboards/powerbi/extracts/:
    region_month.csv   one row per month x region: spend, money in, transactions,
                       active accounts, active cards
    loan_book.csv      one row per loan start year x region: loans, delinquent, defaulted,
                       exposure, delinquency and default rate
    regions.csv        region list (for a slicer / relationships)

Only rpt_ tables are read, plus dim_district / dim_date for region and month labels
(geography and calendar, no personal data). Column names are checked against the
PII ban list before anything is written.

Definitions (also in docs/powerbi-dashboard.md):
    spend            total debits (CZK): the Berka data has no merchant card spend
    active card      account with a card that had at least one transaction in the month
                     (proxy: card issue dates are not in the reporting layer)
    delinquent loan  status running_in_debt (in arrears at the end of the data)
    default          is_default = finished_defaulted or running_in_debt (same as the MI pack)
Loan status is a snapshot at the end of the data, so loan_book is grouped by start year,
not by month of default.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from card_analytics import mask
from card_analytics.load import DEFAULT_DB, connect

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "dashboards" / "powerbi" / "extracts"

REGION_MONTH_SQL = """
select dt.year_month,
       d.region,
       sum(case when f.direction = 'debit'  then f.amount_czk else 0 end) as spend_czk,
       sum(case when f.direction = 'credit' then f.amount_czk else 0 end) as money_in_czk,
       count(*)                                                           as transactions,
       count(distinct f.account_key)                                      as active_accounts,
       count(distinct case when a.n_cards > 0 then f.account_key end)     as active_cards
from rpt_fact_transactions f
join rpt_dim_account a on a.account_key = f.account_key
join dim_date dt       on dt.date_key   = f.date_key
join dim_district d    on d.district_id = f.district_id
group by dt.year_month, d.region
order by dt.year_month, d.region
"""

LOAN_BOOK_SQL = """
select l.date_key / 10000                                              as start_year,
       d.region,
       count(*)                                                        as loans,
       sum(case when l.status = 'running_in_debt' then 1 else 0 end)   as delinquent_loans,
       sum(l.is_default)                                               as defaulted_loans,
       sum(l.amount_czk)                                               as exposure_czk,
       sum(case when l.is_default = 1 then l.amount_czk else 0 end)    as defaulted_exposure_czk
from rpt_fact_loans l
join dim_district d on d.district_id = l.district_id
group by l.date_key / 10000, d.region
order by l.date_key / 10000, d.region
"""


def build(conn) -> dict[str, pd.DataFrame]:
    """The three extract tables. Rates are left to Power BI measures (ratios of sums)."""
    region_month = pd.read_sql(REGION_MONTH_SQL, conn)
    loan_book = pd.read_sql(LOAN_BOOK_SQL, conn)
    regions = pd.DataFrame({"region": sorted(set(region_month.region) | set(loan_book.region))})
    out = {"region_month": region_month, "loan_book": loan_book, "regions": regions}
    for name, df in out.items():
        leaked = set(df.columns) & mask.BANNED_COLUMNS
        if leaked:
            raise ValueError(f"{name} has PII columns: {sorted(leaked)}")
    return out


def write(conn, out_dir: Path = DEFAULT_OUT) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, df in build(conn).items():
        paths[name] = out_dir / f"{name}.csv"
        df.to_csv(paths[name], index=False)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    for name, p in write(connect(args.db), args.out).items():
        print(f"{name}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

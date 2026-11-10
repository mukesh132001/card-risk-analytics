# Power BI dashboard: build guide

One-page dashboard on the PII-masked reporting layer: **spend, active cards, delinquency and default rate by region**. Build it in Power BI Desktop from the CSV extracts, then save a screenshot to `dashboards/powerbi/screenshot.png` and the file as `dashboards/powerbi/card_risk.pbix`.

## 1. Refresh the extracts

```powershell
$env:PYTHONPATH = "src"
python -m card_analytics.bi_extracts --db sqlite:///cardrisk.db
```

Writes three CSVs to `dashboards/powerbi/extracts/` (only masked `rpt_*` tables; no client or account identifiers):

| File | Grain | Columns |
| --- | --- | --- |
| region_month.csv | month x region (576 rows) | year_month, region, spend_czk, money_in_czk, transactions, active_accounts, active_cards |
| loan_book.csv | loan start year x region (47 rows) | start_year, region, loans, delinquent_loans, defaulted_loans, exposure_czk, defaulted_exposure_czk |
| regions.csv | region (8 rows) | region |

## 2. Definitions (put these in the report's info tooltip)

- **Spend**: total debits in CZK. Berka has no merchant card spend, so this is all money out.
- **Active cards**: accounts that hold a card and had at least one transaction in the month. This is a proxy; card issue dates are not in the reporting layer.
- **Delinquent loan**: status `running_in_debt`, i.e. in arrears at the end of the data.
- **Default**: `finished_defaulted` or `running_in_debt` (the same definition as the MI pack and findings memo). Delinquent loans are therefore a subset of defaults.
- Loan status is a **snapshot**, so the loan visuals group by loan start year, never by month of default.

## 3. Load and model

1. **Home > Get data > Text/CSV**, load all three files, **Transform Data** and check types: `spend_czk`, `money_in_czk`, `exposure_czk`, `defaulted_exposure_czk` as Decimal; counts as Whole number; `year_month` as **Text**.
2. In `region_month` add a column (Add column > Custom): `Month = Date.FromText([year_month] & "-01")`, type Date.
3. **Model view**: relate `regions[region]` to `region_month[region]` and `loan_book[region]` (one-to-many, single direction). Use `regions[region]` in the slicer and axes so both fact tables filter together.
4. Create a calendar-free time axis: use `region_month[Month]` as the date axis; for loans use `loan_book[start_year]`.

## 4. Measures (New measure)

```dax
Spend (CZK m)      = DIVIDE ( SUM ( region_month[spend_czk] ), 1000000 )
Active Cards       = SUM ( region_month[active_cards] )          -- use per month; do not total across months
Loans              = SUM ( loan_book[loans] )
Delinquent Loans   = SUM ( loan_book[delinquent_loans] )
Defaulted Loans    = SUM ( loan_book[defaulted_loans] )
Delinquency Rate   = DIVIDE ( [Delinquent Loans], [Loans] )
Default Rate       = DIVIDE ( [Defaulted Loans], [Loans] )
Active Cards (latest month) =
    CALCULATE ( [Active Cards], FILTER ( ALL ( region_month[Month] ), region_month[Month] = MAX ( region_month[Month] ) ) )
```

Always build rates as a ratio of sums, never an average of per-region rates; small regions would be over-weighted.

## 5. Page layout (16:9, 1280 x 720)

```
+--------------------------------------------------------------+
| Card Risk Overview                         [Region slicer]   |  row 1  (title, slicer)
+----------+-----------+-----------+-----------+---------------+
| Spend    | Active    | Loans     | Delinq.   | Default       |  row 2  (5 KPI cards)
| (CZK m)  | cards     |           | rate      | rate          |
+----------+-----------+-----------+-----------+---------------+
| Spend over time (line, Month)   | Default rate by region     |  row 3
|                                 | (bar, sorted desc)         |
+---------------------------------+----------------------------+
| Active cards over time (area)   | Region table: spend, cards,|  row 4
|                                 | loans, delinq. %, default %|
+---------------------------------+----------------------------+
```

| Visual | Fields | Notes |
| --- | --- | --- |
| Slicer | `regions[region]` | Dropdown, multi-select; Edit interactions on if any card should ignore it |
| KPI cards x5 | Spend (CZK m), Active Cards (latest month), Loans, Delinquency Rate, Default Rate | Format rates as 0.0% |
| Line chart | X `region_month[Month]`, Y `[Spend (CZK m)]` | Add a drill-down by year; Y axis starts at 0 |
| Clustered bar | Y `regions[region]`, X `[Default Rate]` | Sort descending; add `[Delinquency Rate]` as a second series |
| Area chart | X `region_month[Month]`, Y `[Active Cards]` | |
| Table | region, `[Spend (CZK m)]`, `[Active Cards (latest month)]`, `[Loans]`, `[Delinquency Rate]`, `[Default Rate]` | Conditional formatting (data bars) on Default Rate |

Style: one accent colour for the headline measure, grey for context; consistent number formats; titles that say what the chart shows, e.g. "Default rate by region (loans started 1993-98)".

## 6. Check it against the source before publishing

With no filters the dashboard should show:

- Loans **682**, defaults **76** (**11.1%**), delinquent **45** (**6.6%**)
- Total spend **CZK 3,030.3 m** over 1993-1998
- Selecting **Dec 1998** gives spend about **CZK 76.5 m** and **884** active cards

If these differ, a relationship or type is wrong. The same numbers appear in the [Excel MI pack](../dashboards/mi_pack.xlsx) and `python -m card_analytics.pipeline` reconciliation.

## 7. Save and add to the repo

1. Save `dashboards/powerbi/card_risk.pbix` (the .pbix embeds data; it is built only from masked extracts).
2. Export the page to `dashboards/powerbi/screenshot.png` and link it from the README.

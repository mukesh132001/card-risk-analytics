-- Analytical SQL practice (issue #3)
-- Run:  python -m card_analytics.ask sql/analytical.sql --db sqlite:///cardrisk.db
-- Each query must start with a line beginning "-- Q" and end with a semicolon.

-- Q1. Rank the top 10 districts by total money moved (CZK), all years.
--
-- Show: rank, district_name, region, transactions, total_czk (in millions, 1 decimal)
--
-- Tables you need:
--   fact_transactions  (district_id, amount_czk)
--   dim_district       (district_id, district_name, region)
--
-- Hints:
--   1. join the two tables on district_id
--   2. group by district, then sum(amount_czk) and count(*)
--   3. rank() over (order by sum(amount_czk) desc) gives the rank
--   4. round(sum(amount_czk) / 1e6, 1) gives millions
--   5. order by the rank and add "limit 10"
--
-- Write your query below this line:
select rank() over (order by sum(f.amount_czk) desc) as rank,
       d.district_name,
       d.region,
       count(f.amount_czk) as transactions,
       round(sum(f.amount_czk) / 1e6, 1) as total_czk
from fact_transactions f
join dim_district d on d.district_id = f.district_id
group by d.district_name, d.region
order by rank
limit 10;
-- Q2. Month-over-month change in money in (credits)
with monthly as (
    select dt.year_month,
           round(sum(f.amount_czk) / 1e6, 2) as money_in_m
    from fact_transactions f
    join dim_date dt on dt.date_key = f.date_key
    where f.direction = 'credit'
    group by dt.year_month
)
select year_month,
       money_in_m,
             round((money_in_m - lag(money_in_m) over (order by year_month))
             / lag(money_in_m) over (order by year_month) * 100, 1) as pct_change
from monthly
order by year_month;

-- Q3. Running balance for one account, checked against the bank's own balance.
--
-- Every row in fact_transactions has signed_amount_czk (+ for credit, - for debit)
-- and balance_after_czk (the balance the bank printed after that transaction).
-- If you add up signed_amount_czk in order, you should get the bank's balance.
-- Let's see if that's true for account 1.
--
-- Show: trans_id, date, direction, amount_czk, running_balance, balance_after_czk,
--       diff (running_balance - balance_after_czk, 2 decimals)
--
-- Tables you need:
--   fact_transactions  (account_id, trans_id, date_key, direction, amount_czk,
--                       signed_amount_czk, balance_after_czk)
--   dim_date           (date_key, date)
--
-- Hints:
--   1. join dim_date on date_key to get a readable date
--   2. filter with  where f.account_id = 1
--   3. running total = sum(f.signed_amount_czk) over (
--          partition by f.account_id
--          order by f.date_key, f.trans_id
--          rows between unbounded preceding and current row)
--   4. you cannot reuse the alias running_balance at the same level, so either
--      repeat the window expression for diff, or wrap it in a CTE first (like Q2)
--   5. order by date_key, trans_id
--
-- When it runs, look at rows where diff is not 0, then answer in a comment:
--   a) What do the mismatched rows have in common? (hint: look at the dates)
--   b) Why would the order of transactions on the same day matter?
--
-- Write your query below this line:
with running as (
    select f.trans_id,
           f.date_key,
           dt.date,
           f.direction,
           f.amount_czk,
           sum(f.signed_amount_czk) over (
               partition by f.account_id
               order by f.date_key, f.trans_id
               rows between unbounded preceding and current row
           ) as running_balance,
           f.balance_after_czk
    from fact_transactions f
    join dim_date dt on dt.date_key = f.date_key
    where f.account_id = 1
)
select trans_id,
       date,
       direction,
       amount_czk,
       round(running_balance, 2)                     as running_balance,
       balance_after_czk,
       round(running_balance - balance_after_czk, 2) as diff
from running
order by date_key, trans_id;


-- Q4. Customer cohorts: accounts grouped by the year of their first transaction,
--     and how many of them are still active in each later year.
with first_txn as (
    select f.account_id,
           min(dt.year) as cohort_year
    from fact_transactions f
    join dim_date dt on dt.date_key = f.date_key
    group by f.account_id
),
activity as (
    select distinct f.account_id, dt.year as active_year
    from fact_transactions f
    join dim_date dt on dt.date_key = f.date_key
)
select c.cohort_year,
       a.active_year,
       count(*) as active_accounts,
       round(100.0 * count(*) / max(count(*)) over (partition by c.cohort_year), 1) as pct_of_cohort
from first_txn c
join activity a on a.account_id = c.account_id
group by c.cohort_year, a.active_year
order by c.cohort_year, a.active_year;

-- Q5. Top 10 districts by overdraft rate (Berka has no fraud labels, so overdrafts
--     stand in as the risk signal). Only districts with 5,000+ transactions.
select rank() over (order by avg(f.is_overdrawn) desc) as rank,
       d.district_name,
       d.region,
       count(*)                               as transactions,
       sum(f.is_overdrawn)                    as overdrawn,
       round(100.0 * avg(f.is_overdrawn), 2)  as overdrawn_pct
from fact_transactions f
join dim_district d on d.district_id = f.district_id
group by d.district_id, d.district_name, d.region
having count(*) >= 5000
order by rank
limit 10;

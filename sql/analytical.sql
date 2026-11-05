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
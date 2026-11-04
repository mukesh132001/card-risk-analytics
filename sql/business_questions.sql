-- Business questions on the Berka star schema. Run in VS Code with SQLTools,
-- or: python -m card_analytics.ask sql/business_questions.sql --db sqlite:///cardrisk.db

-- Q1. Loan default rate by region
select d.region,
       count(*)                                  as loans,
       sum(l.is_default)                         as defaults,
       round(100.0 * sum(l.is_default) / count(*), 1) as default_rate_pct
from fact_loans l
join dim_district d on d.district_id = l.district_id
group by d.region
order by default_rate_pct desc;

-- Q2. Money in vs money out by year (CZK millions)
select dt.year,
       round(sum(case when f.direction = 'credit' then f.amount_czk end) / 1e6, 1) as money_in_m,
       round(sum(case when f.direction = 'debit'  then f.amount_czk end) / 1e6, 1) as money_out_m,
       count(*)                                                                   as transactions
from fact_transactions f
join dim_date dt on dt.date_key = f.date_key
group by dt.year
order by dt.year;

-- Q3. Do customers with a card default less? (loan holders only)
select case when a.n_cards > 0 then 'has card' else 'no card' end as card_holder,
       count(*)                                        as loans,
       round(100.0 * avg(a.loan_is_default), 1)        as default_rate_pct
from dim_account a
where a.has_loan = 1
group by card_holder;

-- Q4. Overdrawn transactions by statement frequency
select a.statement_frequency,
       count(*)                                   as transactions,
       sum(f.is_overdrawn)                        as overdrawn,
       round(100.0 * avg(f.is_overdrawn), 2)      as overdrawn_pct
from fact_transactions f
join dim_account a on a.account_id = f.account_id
group by a.statement_frequency
order by overdrawn_pct desc;

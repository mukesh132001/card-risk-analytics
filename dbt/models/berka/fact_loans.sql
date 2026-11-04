-- One row per loan. Grain: loan_id.
select
    l.loan_id,
    l.account_id,
    a.owner_client_id                                  as client_id,
    a.district_id,
    cast(replace(substr(l.loan_date, 1, 10), '-', '') as integer) as date_key,
    l.amount_czk,
    l.duration                                         as duration_months,
    l.monthly_payment_czk,
    l.status,
    l.is_default
from {{ source('berka', 'loan') }} l
join {{ ref('dim_account') }} a on a.account_id = l.account_id

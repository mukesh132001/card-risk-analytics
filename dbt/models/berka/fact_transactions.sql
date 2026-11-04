-- One row per transaction. Grain: trans_id.
select
    t.trans_id,
    t.account_id,
    a.owner_client_id                                  as client_id,
    a.district_id,
    cast(replace(substr(t.trans_date, 1, 10), '-', '') as integer) as date_key,
    t.direction,
    t.operation,
    t.k_symbol                                         as purpose,
    t.amount_czk,
    t.signed_amount_czk,
    t.balance_after_czk,
    case when t.balance_after_czk < 0 then 1 else 0 end as is_overdrawn
from {{ source('berka', 'trans') }} t
join {{ ref('dim_account') }} a on a.account_id = t.account_id

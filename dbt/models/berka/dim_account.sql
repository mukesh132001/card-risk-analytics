-- One row per account: owner, card and loan status rolled up from the bridge tables.
with owner as (
    select account_id, client_id as owner_client_id, disp_id as owner_disp_id
    from {{ source('berka', 'disp') }}
    where type = 'owner'
),
users as (
    select account_id, count(*) as n_users
    from {{ source('berka', 'disp') }}
    group by account_id
),
cards as (
    select d.account_id, max(c.type) as card_type, count(*) as n_cards
    from {{ source('berka', 'card') }} c
    join {{ source('berka', 'disp') }} d on d.disp_id = c.disp_id
    group by d.account_id
)
select
    a.account_id,
    a.district_id,
    a.frequency                     as statement_frequency,
    a.opened_date,
    o.owner_client_id,
    u.n_users,
    coalesce(cd.n_cards, 0)         as n_cards,
    cd.card_type,
    case when l.loan_id is null then 0 else 1 end as has_loan,
    l.status                        as loan_status,
    coalesce(l.is_default, 0)       as loan_is_default
from {{ source('berka', 'account') }} a
left join owner o  on o.account_id  = a.account_id
left join users u  on u.account_id  = a.account_id
left join cards cd on cd.account_id = a.account_id
left join {{ source('berka', 'loan') }} l on l.account_id = a.account_id

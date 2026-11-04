-- Clean and type the raw transaction feed.
select
    transaction_id,
    customer_id,
    cast("timestamp" as timestamp)      as transaction_ts,
    lower(merchant_category)            as merchant_category,
    upper(country)                      as country,
    cast(amount_usd as numeric(12, 2))  as amount_usd,
    cast(is_fraud as boolean)           as is_fraud
from {{ source('raw', 'transactions') }}

-- Fact table at one row per card transaction.
select
    transaction_id,
    customer_id,
    cast(transaction_ts as date) as date_key,
    merchant_category,
    country,
    amount_usd,
    is_fraud
from {{ ref('stg_transactions') }}

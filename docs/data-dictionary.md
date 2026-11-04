# Data dictionary

| Column | Type | Description | PII |
| --- | --- | --- | --- |
| transaction_id | string | Unique transaction identifier | No |
| customer_id | string | Customer key (masked in reporting layer) | Yes |
| timestamp | timestamp | Time the card was used (UTC) | No |
| merchant_category | string | grocery, travel, dining, fuel, retail, online, utilities | No |
| country | string | ISO country code of the merchant | No |
| amount_usd | decimal(12,2) | Transaction amount in US dollars | No |
| is_fraud | 0/1 | Confirmed fraud label | No |

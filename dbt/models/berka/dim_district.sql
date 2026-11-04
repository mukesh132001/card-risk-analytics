-- One row per district (region facts used to slice every report).
select
    district_id,
    district_name,
    region,
    inhabitants,
    urban_ratio_pct,
    avg_salary_czk,
    unemployment_rate_96,
    entrepreneurs_per_1000,
    crimes_96,
    round(1000.0 * crimes_96 / inhabitants, 2) as crimes_per_1000
from {{ source('berka', 'district') }}

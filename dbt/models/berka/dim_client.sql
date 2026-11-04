-- One row per client, with age band at the end of the data (1998-12-31).
select
    client_id,
    district_id,
    gender,
    birth_date,
    1998 - cast(substr(birth_date, 1, 4) as integer) as age_1998,
    case
        when 1998 - cast(substr(birth_date, 1, 4) as integer) < 25 then '18-24'
        when 1998 - cast(substr(birth_date, 1, 4) as integer) < 35 then '25-34'
        when 1998 - cast(substr(birth_date, 1, 4) as integer) < 50 then '35-49'
        when 1998 - cast(substr(birth_date, 1, 4) as integer) < 65 then '50-64'
        else '65+'
    end as age_band
from {{ source('berka', 'client') }}

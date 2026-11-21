SELECT a.merchant_id, a.currency, CAST(c.captured_at_utc AS DATE) AS utc_day,
       CAST(SUM(c.amount) AS DECIMAL(18,2)) AS captured
FROM captures AS c
JOIN authorisations AS a ON c.auth_id = a.auth_id
GROUP BY a.merchant_id, a.currency, CAST(c.captured_at_utc AS DATE)

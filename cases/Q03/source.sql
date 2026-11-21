SELECT m.merchant_id, CAST(c.captured_at_utc AT TIME ZONE m.settlement_tz AS DATE) AS local_day,
       CAST(SUM(c.amount) AS DECIMAL(18,2)) AS captured
FROM captures AS c
JOIN authorisations AS a ON a.auth_id = c.auth_id
JOIN merchants AS m ON m.merchant_id = a.merchant_id
GROUP BY m.merchant_id, CAST(c.captured_at_utc AT TIME ZONE m.settlement_tz AS DATE)

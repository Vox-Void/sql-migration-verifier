WITH ranked AS (
    SELECT a.merchant_id, c.capture_id, c.captured_at_utc,
           ROW_NUMBER() OVER (
              PARTITION BY a.merchant_id
              ORDER BY c.captured_at_utc DESC NULLS LAST, c.capture_id ASC NULLS LAST
           ) AS ranking
    FROM captures AS c
    JOIN authorisations AS a ON a.auth_id = c.auth_id
)
SELECT merchant_id, capture_id, captured_at_utc
FROM ranked
WHERE ranking <= 3
ORDER BY merchant_id ASC NULLS LAST, captured_at_utc DESC NULLS LAST, capture_id ASC NULLS LAST

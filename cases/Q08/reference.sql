WITH capture_totals AS (
    SELECT auth_id, SUM(amount) AS captured
    FROM captures
    GROUP BY auth_id
)
SELECT a.auth_id,
       CAST(COALESCE(c.captured, 0.00) / NULLIF(a.amount, 0.00) AS DECIMAL(18,4)) AS ratio
FROM authorisations AS a
LEFT JOIN capture_totals AS c ON a.auth_id = c.auth_id
WHERE a.status = 'APPROVED'

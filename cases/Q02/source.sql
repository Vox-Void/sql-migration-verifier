WITH refund_totals AS (
    SELECT capture_id, SUM(amount) AS refunded
    FROM refunds
    GROUP BY capture_id
)
SELECT c.capture_id, CAST(c.amount - COALESCE(r.refunded, 0.00) AS DECIMAL(18,2)) AS net_amount
FROM captures AS c
LEFT JOIN refund_totals AS r ON r.capture_id = c.capture_id

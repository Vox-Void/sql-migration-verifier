WITH ranked AS (
    SELECT e.merchant_id, e.source_ref, e.event_id, e.net_amount,
           ROW_NUMBER() OVER (
               PARTITION BY e.merchant_id, e.source_ref
               ORDER BY e.recorded_at_utc DESC NULLS LAST, e.event_id DESC NULLS LAST
           ) AS ranking
    FROM settlement_events AS e
    CROSS JOIN migration_params AS p
    WHERE e.source_ref IS NOT NULL AND e.recorded_at_utc <= p.as_of_utc
)
SELECT merchant_id, source_ref, event_id, CAST(net_amount AS DECIMAL(18,2)) AS net_amount
FROM ranked
WHERE ranking = 1

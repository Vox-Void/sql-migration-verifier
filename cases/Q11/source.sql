SELECT source_ref, CAST(net_amount AS DECIMAL(18,2)) AS net_amount
FROM settlement_events
WHERE event_id IN ('E0', 'E1', 'E2')
UNION ALL
SELECT source_ref, CAST(net_amount AS DECIMAL(18,2)) AS net_amount
FROM settlement_events
WHERE event_id = 'E3'

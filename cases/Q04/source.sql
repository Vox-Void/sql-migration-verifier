SELECT source_ref, CAST(net_amount AS DECIMAL(18,2)) AS net_amount
FROM settlement_events
WHERE merchant_id = 'M1'

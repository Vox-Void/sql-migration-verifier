SELECT l.event_id, r.event_id AS matched_event_id
FROM settlement_events AS l
LEFT JOIN settlement_events AS r
    ON r.event_id = 'E8' AND l.source_ref IS NOT DISTINCT FROM r.source_ref
WHERE l.event_id IN ('E4', 'E5', 'E6')

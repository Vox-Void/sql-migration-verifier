SELECT capture_id, captured_at_utc
FROM captures
WHERE captured_at_utc >= CAST('2026-01-07T21:59:59.999999+00:00' AS TIMESTAMP)
  AND captured_at_utc <= CAST('2026-01-08T00:00:00+02:00' AS TIMESTAMP)

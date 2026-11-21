SELECT COUNT(*) AS authorisation_count, CAST(SUM(amount) AS DECIMAL(18,2)) AS total
FROM authorisations
WHERE status = 'PENDING'

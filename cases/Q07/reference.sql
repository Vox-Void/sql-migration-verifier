SELECT a.auth_id, a.merchant_id
FROM authorisations AS a
WHERE a.status = 'APPROVED'
  AND NOT EXISTS (SELECT 1 FROM captures AS c WHERE c.auth_id = a.auth_id)

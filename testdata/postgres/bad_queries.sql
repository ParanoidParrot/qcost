-- testdata/postgres/bad_queries.sql
-- These queries should all score medium or above.
-- Used by the self-test CI job and local smoke tests.

-- FULL_TABLE_SCAN_NO_WHERE + SELECT_STAR (score: ~45)
SELECT * FROM users;

-- CARTESIAN_JOIN (score: ~40)
SELECT u.name, o.total
FROM users u, orders o
WHERE u.id = 1;

-- LEADING_WILDCARD_LIKE (score: ~25)
SELECT id, email
FROM users
WHERE email LIKE '%@gmail.com';

-- FUNCTION_ON_INDEXED_COLUMN (score: ~20)
SELECT id
FROM users
WHERE LOWER(email) = 'alice@example.com';

-- SUBQUERY_IN_WHERE (score: ~15)
SELECT *
FROM orders
WHERE user_id IN (
    SELECT id FROM users WHERE created_at > NOW() - INTERVAL '30 days'
);

-- ORDER_BY without LIMIT (score: ~8)
SELECT id, created_at
FROM events
ORDER BY created_at DESC;
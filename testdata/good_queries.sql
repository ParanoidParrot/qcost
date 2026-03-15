-- testdata/postgres/good_queries.sql
-- These queries should all score 0 (LOW tier).
-- Used to verify the analyzer doesn't false-positive on well-written SQL.

-- Explicit columns, indexed WHERE, LIMIT — score: 0
SELECT id, email, created_at
FROM users
WHERE id = $1
LIMIT 1;

-- Proper JOIN with ON condition — score: 0
SELECT u.name, o.total
FROM users u
JOIN orders o ON o.user_id = u.id
WHERE u.id = $1;

-- Paginated query — score: 0
SELECT id, title, body
FROM posts
WHERE published = true
  AND created_at > $1
ORDER BY created_at DESC
LIMIT 20 OFFSET $2;

-- DELETE with WHERE — score: 0
DELETE FROM sessions
WHERE expires_at < NOW();

-- INSERT — score: 0
INSERT INTO audit_log (user_id, action, created_at)
VALUES ($1, $2, NOW());
# qcost

> Query cost predictor for your PR pipeline. Know the execution cost of every SQL query before it reaches production.

[![CI](https://github.com/ParanoidParrot/qcost/actions/workflows/ci.yml/badge.svg)](https://github.com/ParanoidParrot/qcost/actions)

[![PyPI](https://img.shields.io/pypi/v/qcost)](https://pypi.org/project/qcost/)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What it does

**qcost** scans SQL queries in your pull requests — in `.sql` files, Python, Go, and TypeScript source — predicts their execution cost using static AST analysis, and posts a structured cost report as a PR comment. It can optionally connect to a staging database and run real `EXPLAIN` analysis.

It uses [sqlglot](https://github.com/tobymao/sqlglot) for dialect-aware SQL parsing, which means it understands the difference between Postgres, MySQL, and SQLite syntax — and won't false-positive on valid dialect-specific constructs.

**It predicts cost issues like:**
- Full table scans (`SELECT id FROM users` with no WHERE or LIMIT)
- Cartesian products (comma-separated FROM or JOIN without ON)
- Leading wildcard `LIKE` (`LIKE '%term'` — always a full scan)
- Functions on indexed columns (`WHERE LOWER(email) = ...`)
- Correlated subqueries in WHERE clauses
- ORDER BY without LIMIT on large tables
- Implicit type casts (`WHERE id = '123'`)

---

## Example PR comment

```
## 🔍 QCost Report

![fail](https://img.shields.io/badge/gate-FAIL-red)  Total cost score: 88   Queries analysed: 3

| File | Query | Tier | Score | Issues |
|------|-------|------|-------|--------|
| migrations/001.sql:4 | SELECT * FROM users | 🔴 CRITICAL | 45 | 2 |
| db/queries.py:88     | SELECT id FROM events ORDER... | 🟠 HIGH | 28 | 1 |
| db/queries.py:102    | SELECT id FROM users WHERE LOWER... | 🟡 MEDIUM | 15 | 1 |
```

---

## GitHub Action

```yaml
# .github/workflows/qcost.yml
name: QCost
on:
  pull_request:
    paths:
      - '**/*.sql'
      - '**/migrations/**'
      - '**/db/**'

jobs:
  query-cost:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: your-username/qcost@v1
        with:
          db-type: postgres
          fail-on-high: 'true'
```

### With live EXPLAIN (optional)

Point qcost at a staging database to get real planner cost figures instead of heuristic estimates:

```yaml
      - uses: your-username/qcost@v1
        with:
          db-type: postgres
          dsn: ${{ secrets.STAGING_DB_DSN }}
          fail-on-high: 'true'
```

---

## Configuration

Create `.qcost.yml` in your repo root:

```yaml
db:
  type: postgres        # postgres | mysql | sqlite
  # dsn: "postgres://..." # optional, enables live EXPLAIN

thresholds:
  fail_score: 75        # PR gate fails at or above this score (0–100)
  warn_score: 40        # warning only below fail_score
  max_rows: 500000      # flag queries estimated to return more rows than this

scan:
  include:
    - "**/*.sql"
    - "**/migrations/**/*.py"
    - "**/db/**/*.py"
  exclude:
    - "vendor/**"
    - "**/test_*.py"

output:
  format: text          # text | json | markdown
  verbose: false        # include EXPLAIN plan in output (requires dsn)
```

---

## Cost scoring

Each detected issue adds penalty points to a 0–100 score:

| Issue | Penalty | Why |
|-------|---------|-----|
| Cartesian join | +40 | Multiplies row count of every joined table |
| Full table scan | +30 | Reads every row with no filter |
| Leading wildcard LIKE | +25 | Can't use B-tree index |
| Function on indexed column | +20 | Index unusable when column is wrapped |
| Subquery in WHERE | +15 | May execute once per outer row |
| SELECT * | +15 | Fetches unused columns incl. large BLOBs |
| ORDER BY without LIMIT | +8 | Full sort of result set |
| Implicit type cast | +10 | Forces per-row cast, index skipped |
| OR in WHERE | +5 | May prevent index use |

| Score | Tier | Badge |
|-------|------|-------|
| 0–19  | Low  | 🟢 |
| 20–44 | Medium | 🟡 |
| 45–69 | High | 🟠 |
| 70–100 | Critical | 🔴 |

In live EXPLAIN mode, real planner row estimates and total cost override the heuristic score when higher.

---

## Supported databases

| Database | Heuristic | Live EXPLAIN |
|----------|-----------|--------------|
| PostgreSQL | ✅ | ✅ `EXPLAIN (FORMAT JSON)` |
| MySQL | ✅ | ✅ `EXPLAIN FORMAT=JSON` |
| SQLite | ✅ | ✅ `EXPLAIN QUERY PLAN` |

## Supported source file types

`.sql` · `.py` · `.go` · `.ts` · `.tsx` · `.js` · `.jsx`

---

## Why Python

qcost uses [sqlglot](https://github.com/tobymao/sqlglot) for SQL parsing. sqlglot produces a full AST for 20+ SQL dialects, which means rules operate on actual parse tree nodes — not regex over raw SQL text. This eliminates false positives that plague string-matching approaches: a `LIKE '%term'` inside a comment won't fire the wildcard rule; a `LOWER()` call on a literal won't fire the function-on-column rule.

---

## License

MIT
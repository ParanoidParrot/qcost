# qcost

> SQL query cost predictor for your PR pipeline. Know the execution cost of every SQL query before it reaches production.

[![CI](https://github.com/ParanoidParrot/qcost/actions/workflows/ci.yml/badge.svg)](https://github.com/ParanoidParrot/qcost/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/ParanoidParrot/qcost/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![sqlglot](https://img.shields.io/badge/parser-sqlglot-orange)](https://github.com/tobymao/sqlglot)

---

## What it does

**qcost** scans SQL queries in your pull requests — in `.sql` files, Python, Go, and TypeScript source — predicts their execution cost using AST-based static analysis, and posts a structured cost report as a PR comment.

It uses [sqlglot](https://github.com/tobymao/sqlglot) for dialect-aware SQL parsing. Rules operate on actual AST nodes — not regex over raw SQL text — which means fewer false positives and precise dialect-specific analysis for Postgres, MySQL, and SQLite.

**Detects:**
- Full table scans (`SELECT id FROM users` with no WHERE or LIMIT)
- Cartesian products (comma-separated FROM or JOIN without ON)
- Leading wildcard `LIKE` (`LIKE '%term'` — always a full scan)
- Functions on indexed columns (`WHERE LOWER(email) = ...`)
- Correlated subqueries in WHERE clauses
- ORDER BY without LIMIT on large tables
- Implicit type casts (`WHERE id = '123'`)

---

## Demo

> Open a PR that adds a query — qcost comments automatically.

![qcost PR comment demo](docs/demo.png)

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
      - uses: ParanoidParrot/qcost@v1
        with:
          db-type: postgres
          fail-on-high: 'true'
```

### With live EXPLAIN (optional)

```yaml
      - uses: ParanoidParrot/qcost@v1
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
  verbose: false
```

---

## Cost scoring

| Issue | Penalty | Why |
|-------|---------|-----|
| Cartesian join | +40 | Multiplies row count of every joined table |
| Full table scan | +30 | Reads every row with no filter |
| Leading wildcard LIKE | +25 | Cannot use B-tree index |
| Function on indexed column | +20 | Index unusable when column is wrapped |
| Subquery in WHERE | +15 | May execute once per outer row |
| SELECT * | +15 | Fetches unused columns including large BLOBs |
| ORDER BY without LIMIT | +8 | Full sort of result set |
| Implicit type cast | +10 | Forces per-row cast, index skipped |
| OR in WHERE | +5 | May prevent index use |

| Score | Tier | Badge |
|-------|------|-------|
| 0–19  | Low  | 🟢 |
| 20–44 | Medium | 🟡 |
| 45–69 | High | 🟠 |
| 70–100 | Critical | 🔴 |

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

qcost uses [sqlglot](https://github.com/tobymao/sqlglot) for SQL parsing, which produces a full AST for 20+ SQL dialects. Rules operate on actual parse tree nodes rather than regex over raw SQL. This eliminates false positives — a `LIKE '%term'` inside a comment won't fire the wildcard rule; a `LOWER()` call on a literal won't fire the function-on-column rule.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add rules and run tests locally.

## License

[MIT](LICENSE)
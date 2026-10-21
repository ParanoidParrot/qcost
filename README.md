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

## Where this sits
 
Most tools in this space lint **schema migrations** — [Squawk](https://github.com/sbdchd/squawk) and [Atlas](https://github.com/ariga/atlas-action) check DDL for operations that lock tables or break compatibility. qcost checks **queries**, which is a different question, and the two are complementary.
 
Of the tools that do look at query cost, most target data warehouses (Snowflake, BigQuery) or are commercial platforms that require sending your queries to a hosted service. qcost is aimed at OLTP — Postgres, MySQL, SQLite — runs in your own CI, and is MIT licensed.
 
It is an actively developed prototype. See [Known limitations](#known-limitations).
 
---

## Demo

> Open a PR that adds a query — qcost comments automatically.

<img width="1340" height="764" alt="Screenshot 2026-03-24 at 1 19 35 AM" src="https://github.com/user-attachments/assets/415d89d3-a3f1-4692-9144-ed9af463e974" />


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

## How live EXPLAIN changes the score
 
Static rules have to assume the worst: a leading-wildcard `LIKE` *might* scan ten million rows. When a DSN is configured, qcost asks your database's planner instead — and the planner's answer can move the score **in either direction**.
 
- **The planner says the query is cheap** → the static penalty is lowered. A `LIKE '%@corp.com'` against a 140-row lookup table is not a performance problem, and qcost stops reporting it as one.
- **The planner says the query is expensive** → the score is raised above whatever the static rules found.
- **No DSN, or the planner gives no usable estimate** → the static score stands unchanged.
The PR comment states whenever a score was adjusted, and shows the planner cost and row estimate it used, so an adjustment is never silent.
 
**Structural defects are never discounted.** A cartesian join against an almost-empty staging database *is* cheap today, and will still take production down when the tables grow. `CARTESIAN_JOIN` and `MISSING_JOIN_CONDITION` therefore keep their full penalty however cheap the plan looks.
 
> **The plan is only as good as the statistics behind it.** A DSN pointing at a near-empty database will report almost everything as cheap and will suppress real findings. Point `dsn` at a database whose table sizes and statistics resemble production, or leave it unset and rely on static analysis.

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

## Local use
 
```bash
pip install -e .
qcost path/to/queries.sql
echo "SELECT * FROM users" | qcost -
```

---

## Known limitations
 
- `scan.include` and `scan.exclude` are parsed but not yet applied; file selection currently filters on extension only
- `thresholds.warn_score` is accepted and validated but does not yet affect the report
- `output.format: text` falls back to markdown when run inside the Action
- The gate is evaluated per query, but the failure message reports the total score across all queries
- SQLite cannot lower a score, because `EXPLAIN QUERY PLAN` exposes no cost or row estimates
- Queries built by string concatenation or an ORM are only detected when a complete SQL literal appears in the source
---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add rules and run tests locally.

## License

[MIT](LICENSE)

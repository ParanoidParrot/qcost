"""
qcost.explainer
~~~~~~~~~~~~~~~~~~~~
Connects to a live database and runs EXPLAIN to get real planner stats.
Only used when cfg.db.dsn is set — always optional.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from qcost.models import DBType

log = logging.getLogger(__name__)


@dataclass
class ExplainResult:
    plan_text:      str
    estimated_rows: int     # -1 = unknown
    planner_cost:   float   # Postgres total_cost; -1 for MySQL/SQLite


def run(dsn: str, db_type: DBType, sql: str) -> ExplainResult:
    """Run EXPLAIN against a live DB and return structured results."""
    if db_type == DBType.POSTGRES:
        return _explain_postgres(dsn, sql)
    if db_type == DBType.MYSQL:
        return _explain_mysql(dsn, sql)
    if db_type == DBType.SQLITE:
        return _explain_sqlite(dsn, sql)
    raise ValueError(f"Unsupported db_type: {db_type}")


# ── Postgres ──────────────────────────────────────────────────────────────────

def _explain_postgres(dsn: str, sql: str) -> ExplainResult:
    import psycopg2  # type: ignore

    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (FORMAT JSON, ANALYZE false, BUFFERS false) {sql}")
            rows = cur.fetchall()
    finally:
        conn.close()

    plan_json: list[dict[str, Any]] = rows[0][0]
    plan_text = json.dumps(plan_json, indent=2)

    top = plan_json[0].get("Plan", {})
    return ExplainResult(
        plan_text      = plan_text,
        estimated_rows = int(top.get("Plan Rows", -1)),
        planner_cost   = float(top.get("Total Cost", -1)),
    )


# ── MySQL ─────────────────────────────────────────────────────────────────────

def _explain_mysql(dsn: str, sql: str) -> ExplainResult:
    import pymysql  # type: ignore

    # DSN format: mysql://user:pass@host:port/db  →  parse for pymysql kwargs.
    conn = pymysql.connect(**_parse_mysql_dsn(dsn))
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(f"EXPLAIN FORMAT=JSON {sql}")
                row = cur.fetchone()
                plan_text = row[0] if row else "{}"
                parsed = json.loads(plan_text)
                rows = int(
                    parsed.get("query_block", {})
                          .get("table", {})
                          .get("rows_examined_per_scan", -1)
                )
            except Exception:
                # Fallback: traditional EXPLAIN.
                cur.execute(f"EXPLAIN {sql}")
                rows_list = cur.fetchall()
                plan_text = "\n".join("\t".join(str(v) for v in r) for r in rows_list)
                rows = -1
    finally:
        conn.close()

    return ExplainResult(plan_text=plan_text, estimated_rows=rows, planner_cost=-1)


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    """Convert mysql://user:pass@host:port/db to pymysql connect kwargs."""
    from urllib.parse import urlparse
    u = urlparse(dsn)
    return {
        "host":   u.hostname or "localhost",
        "port":   u.port or 3306,
        "user":   u.username,
        "password": u.password,
        "database": u.path.lstrip("/"),
        "cursorclass": __import__("pymysql.cursors", fromlist=["DictCursor"]).DictCursor,
    }


# ── SQLite ────────────────────────────────────────────────────────────────────

def _explain_sqlite(dsn: str, sql: str) -> ExplainResult:
    import sqlite3

    # DSN is just a file path for SQLite.
    db_path = dsn.replace("sqlite:///", "").replace("sqlite://", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(f"EXPLAIN QUERY PLAN {sql}")
        rows = cur.fetchall()
    finally:
        conn.close()

    plan_text = "\n".join(f"{r['id']}|{r['parent']}|{r['detail']}" for r in rows)
    has_full_scan = any(
        "SCAN" in r["detail"].upper() and "SEARCH" not in r["detail"].upper()
        for r in rows
    )
    # SQLite doesn't expose row estimates — use MaxInt as a sentinel for full scan.
    estimated_rows = 2**31 if has_full_scan else -1

    return ExplainResult(
        plan_text      = plan_text,
        estimated_rows = estimated_rows,
        planner_cost   = -1,
    )
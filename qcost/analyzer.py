"""
qcost.analyzer
~~~~~~~~~~~~~~~
Orchestrates the full analysis pipeline:

    source file
        → extractor  (finds SQL strings + line numbers)
        → sqlglot    (parses SQL → AST, per dialect)
        → rules      (walks AST, emits Issues)
        → explainer  (optional: live EXPLAIN via DB connection)
        → QueryResult
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import sqlglot

if TYPE_CHECKING:
    from qcost.explainer import ExplainResult

from qcost.config import Config
from qcost.extractors import from_file, ExtractedQuery
from qcost.models import (
    CostTier, DBType, Issue, QueryResult, Report, score_to_tier,
)
from qcost.rules import RULES, SCORE_MAP

log = logging.getLogger(__name__)

_DIALECT_MAP = {
    DBType.POSTGRES: "postgres",
    DBType.MYSQL:    "mysql",
    DBType.SQLITE:   "sqlite",
}


# ── Public API ────────────────────────────────────────────────────────────────

def run_file(path: str | Path, cfg: Config) -> list[QueryResult]:
    """Extract and analyse all queries in *path*."""
    queries = from_file(path)
    return [_analyse_one(q, cfg) for q in queries]


def run_sql(sql: str, label: str, cfg: Config) -> QueryResult:
    """Analyse a raw SQL string (used by stdin mode)."""
    q = ExtractedQuery(sql=sql, file=label, line=0)
    return _analyse_one(q, cfg)


def build_report(results: list[QueryResult], cfg: Config) -> Report:
    total = sum(r.score for r in results)
    pass_gate = all(r.score < cfg.thresholds.fail_score for r in results)
    unique_files = len({r.file for r in results})
    summary = (
        f"Analyzed {len(results)} quer{'ies' if len(results) != 1 else 'y'} "
        f"across {unique_files} file(s). Total cost score: {total}."
    )
    return Report(results=results, total_cost=total, pass_gate=pass_gate, summary=summary)


# ── Internal ──────────────────────────────────────────────────────────────────

def _analyse_one(q: ExtractedQuery, cfg: Config) -> QueryResult:
    dialect = _DIALECT_MAP[cfg.db.type]
    issues, score = _heuristic(q.sql, dialect)
    tier = score_to_tier(score)

    result = QueryResult(
        query=q.sql,
        file=q.file,
        line=q.line,
        db_type=cfg.db.type,
        tier=tier,
        score=score,
        issues=issues,
    )

    if cfg.db.dsn:
        try:
            from qcost.explainer import run as explain_run, ExplainResult
            plan: ExplainResult = explain_run(cfg.db.dsn, cfg.db.type, q.sql)
            result.explain_plan   = plan.plan_text
            result.estimated_rows = plan.estimated_rows
            explain_score = _score_from_explain(plan, cfg)
            if explain_score > score:
                result.score = explain_score
                result.tier  = score_to_tier(explain_score)
        except Exception as exc:
            log.debug("EXPLAIN failed for %s:%d — %s (using heuristic)", q.file, q.line, exc)

    return result


def _heuristic(sql: str, dialect: str) -> tuple[list[Issue], int]:
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect, error_level=sqlglot.ErrorLevel.WARN)
    except sqlglot.errors.ParseError as exc:
        log.debug("sqlglot parse error (%s): %s", dialect, exc)
        issue = Issue(
            code="PARSE_ERROR",
            severity=CostTier.LOW,
            message=f"Could not parse query as {dialect} SQL: {exc}",
            suggestion="Check the query syntax; analysis may be incomplete.",
        )
        return [issue], 5

    issues: list[Issue] = []
    for rule in RULES:
        try:
            issues.extend(rule(ast, dialect))
        except Exception as exc:
            log.debug("Rule %s raised: %s", rule.__name__, exc)

    seen: set[str] = set()
    deduped: list[Issue] = []
    for issue in issues:
        if issue.code not in seen:
            seen.add(issue.code)
            deduped.append(issue)

    score = min(100, sum(SCORE_MAP.get(i.code, 5) for i in deduped))
    return deduped, score


def _score_from_explain(plan: "ExplainResult", cfg: Config) -> int:
    score = 0
    if plan.estimated_rows > cfg.thresholds.max_rows:
        score += 40
    elif plan.estimated_rows > cfg.thresholds.max_rows // 10:
        score += 20
    if plan.planner_cost > 100_000:
        score += 40
    elif plan.planner_cost > 10_000:
        score += 20
    elif plan.planner_cost > 1_000:
        score += 10
    return min(100, score)
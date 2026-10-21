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
from qcost.extractors import ExtractedQuery, from_file
from qcost.models import (
    CostTier,
    DBType,
    Issue,
    QueryResult,
    Report,
    ScoreSource,
    score_to_tier,
)
from qcost.rules import RULES, SCORE_MAP, STRUCTURAL_CODES

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
    issues, heuristic_score = _heuristic(q.sql, dialect)

    result = QueryResult(
        query=q.sql,
        file=q.file,
        line=q.line,
        db_type=cfg.db.type,
        tier=score_to_tier(heuristic_score),
        score=heuristic_score,
        heuristic_score=heuristic_score,
        issues=issues,
        score_source=ScoreSource.HEURISTIC,
    )

    if not cfg.db.dsn:
        return result

    try:
        from qcost.explainer import run as explain_run
        plan: ExplainResult = explain_run(cfg.db.dsn, cfg.db.type, q.sql)
    except Exception as exc:
        # A configured DSN that does not work is a setup error the user needs
        # to see — silently falling back to heuristics makes a broken
        # connection indistinguishable from a working one.
        log.warning(
            "EXPLAIN failed for %s:%d (%s) — falling back to heuristic score",
            q.file, q.line, exc,
        )
        return result

    result.explain_plan   = plan.plan_text
    result.estimated_rows = plan.estimated_rows
    result.planner_cost   = plan.planner_cost

    explain_score = _score_from_explain(plan, cfg)
    result.score, result.score_source = _reconcile(
        heuristic_score, explain_score, plan, issues, cfg
    )
    result.tier = score_to_tier(result.score)

    if result.score < heuristic_score:
        log.info(
            "%s:%d — plan lowered score %d → %d (planner_cost=%.1f, rows=%d)",
            q.file, q.line, heuristic_score, result.score,
            plan.planner_cost, plan.estimated_rows,
        )

    return result


def _reconcile(
    heuristic_score: int,
    explain_score: int,
    plan: ExplainResult,
    issues: list[Issue],
    cfg: Config,
) -> tuple[int, ScoreSource]:
    """Combine the static score with the live planner's verdict.

    The planner has measured this query against real table statistics, so when
    it reports the query as inexpensive it is better evidence than a static
    guess — a leading-wildcard LIKE against a 200-row lookup table is not a
    problem, however much the AST rule dislikes it.

    Structural defects are the exception: see STRUCTURAL_CODES.
    """
    structural_floor = min(
        100,
        sum(SCORE_MAP.get(i.code, 5) for i in issues if i.code in STRUCTURAL_CODES),
    )

    if _plan_says_cheap(plan, cfg):
        adjusted = max(explain_score, structural_floor)
        if adjusted < heuristic_score:
            return adjusted, ScoreSource.PLAN_CHEAP
        return heuristic_score, ScoreSource.HEURISTIC

    if explain_score > heuristic_score:
        return explain_score, ScoreSource.PLAN

    return heuristic_score, ScoreSource.HEURISTIC


def _plan_says_cheap(plan: ExplainResult, cfg: Config) -> bool:
    """True only when the planner gives a trustworthy, genuinely cheap verdict.

    Row and cost estimates of -1 mean 'unknown', which is never grounds for
    discounting a heuristic finding.
    """
    row_ceiling = max(1, cfg.thresholds.max_rows // 10)

    # Postgres reports a real planner cost; require both signals to agree.
    if plan.planner_cost >= 0:
        return (
            plan.planner_cost < cfg.thresholds.cheap_cost
            and 0 <= plan.estimated_rows < row_ceiling
        )

    # MySQL exposes no cost, but a concrete row estimate is still a signal.
    #
    # SQLite's EXPLAIN QUERY PLAN reports neither cost nor row estimates — the
    # explainer uses 2**31 as a full-scan sentinel and -1 for unknown, and both
    # fall outside this range, so SQLite can never justify lowering a score.
    return 0 <= plan.estimated_rows < row_ceiling


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
            issues.extend(rule(ast, dialect))  # type: ignore[arg-type]
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


def _score_from_explain(plan: ExplainResult, cfg: Config) -> int:
    """Score a query from planner statistics alone. -1 means 'unknown'."""
    score = 0
    if plan.estimated_rows >= 0:
        if plan.estimated_rows > cfg.thresholds.max_rows:
            score += 40
        elif plan.estimated_rows > cfg.thresholds.max_rows // 10:
            score += 20
    if plan.planner_cost >= 0:
        if plan.planner_cost > 100_000:
            score += 40
        elif plan.planner_cost > 10_000:
            score += 20
        elif plan.planner_cost > 1_000:
            score += 10
    return min(100, score)
"""
qcost.models
~~~~~~~~~~~~~
Shared data structures used across the entire package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DBType(str, Enum):
    POSTGRES = "postgres"
    MYSQL    = "mysql"
    SQLITE   = "sqlite"


class CostTier(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


TIER_EMOJI = {
    CostTier.LOW:      "🟢",
    CostTier.MEDIUM:   "🟡",
    CostTier.HIGH:     "🟠",
    CostTier.CRITICAL: "🔴",
}


@dataclass
class Issue:
    code:       str
    severity:   CostTier
    message:    str
    suggestion: str
    line:       int = 0


class ScoreSource(str, Enum):
    """Where a query's final score came from."""
    HEURISTIC   = "heuristic"      # static AST rules only
    PLAN        = "plan"           # live EXPLAIN raised the score
    PLAN_CHEAP  = "plan-cheap"     # live EXPLAIN lowered the score


@dataclass
class QueryResult:
    query:          str
    file:           str
    line:           int
    db_type:        DBType
    tier:           CostTier
    score:          int
    issues:         list[Issue] = field(default_factory=list)
    explain_plan:   str | None = None
    estimated_rows: int = -1
    planner_cost:   float = -1.0
    # Score from the static rules alone, kept for comparison when a live
    # EXPLAIN plan overrides it in either direction.
    heuristic_score: int = -1
    score_source:   ScoreSource = ScoreSource.HEURISTIC


@dataclass
class Report:
    results:    list[QueryResult]
    total_cost: int
    pass_gate:  bool
    summary:    str


def score_to_tier(score: int) -> CostTier:
    if score >= 70:
        return CostTier.CRITICAL
    if score >= 45:
        return CostTier.HIGH
    if score >= 20:
        return CostTier.MEDIUM
    return CostTier.LOW
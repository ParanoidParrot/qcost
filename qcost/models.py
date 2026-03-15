"""
qcost.models
~~~~~~~~~~~~~~~~~
Shared data structures used across the entire package.
Kept as plain dataclasses so they serialise cleanly to JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
    line:       int = 0   # 1-based line within the query string; 0 = unknown


@dataclass
class QueryResult:
    query:          str
    file:           str
    line:           int        # 1-based line in the source file
    db_type:        DBType
    tier:           CostTier
    score:          int        # 0–100 composite cost score
    issues:         list[Issue] = field(default_factory=list)
    explain_plan:   Optional[str] = None
    estimated_rows: int = -1   # -1 = unknown (heuristic mode)


@dataclass
class Report:
    results:    list[QueryResult]
    total_cost: int
    pass_gate:  bool
    summary:    str


def score_to_tier(score: int) -> CostTier:
    if score >= 70: return CostTier.CRITICAL
    if score >= 45: return CostTier.HIGH
    if score >= 20: return CostTier.MEDIUM
    return CostTier.LOW
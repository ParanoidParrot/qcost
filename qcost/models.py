"""
qcost.models
~~~~~~~~~~~~~
Shared data structures used across the entire package.
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
    line:       int = 0


@dataclass
class QueryResult:
    query:          str
    file:           str
    line:           int
    db_type:        DBType
    tier:           CostTier
    score:          int
    issues:         list[Issue] = field(default_factory=list)
    explain_plan:   Optional[str] = None
    estimated_rows: int = -1


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
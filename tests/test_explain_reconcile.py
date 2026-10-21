"""Tests for how a live EXPLAIN plan reconciles with the static heuristics.

These exercise the reconciliation logic directly with synthetic ExplainResult
objects, so they need no database connection.
"""
from __future__ import annotations

import pytest

from qcost.analyzer import _plan_says_cheap, _reconcile, _score_from_explain
from qcost.config import Config
from qcost.explainer import ExplainResult
from qcost.models import CostTier, Issue, ScoreSource


def issue(code: str) -> Issue:
    return Issue(code=code, severity=CostTier.MEDIUM, message="", suggestion="")


@pytest.fixture
def cfg() -> Config:
    return Config()


# ── _plan_says_cheap ─────────────────────────────────────────────────────────

def test_postgres_cheap_plan_is_trusted(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=120, planner_cost=8.4)
    assert _plan_says_cheap(plan, cfg) is True


def test_postgres_expensive_plan_is_not_cheap(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=120, planner_cost=250_000.0)
    assert _plan_says_cheap(plan, cfg) is False


def test_postgres_cheap_cost_but_huge_rows_is_not_cheap(cfg):
    # Both signals must agree.
    plan = ExplainResult(plan_text="", estimated_rows=9_000_000, planner_cost=10.0)
    assert _plan_says_cheap(plan, cfg) is False


def test_unknown_rows_never_counts_as_cheap(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=-1, planner_cost=5.0)
    assert _plan_says_cheap(plan, cfg) is False


def test_mysql_row_estimate_alone_can_be_cheap(cfg):
    # MySQL reports no planner cost (-1) but a real row estimate.
    plan = ExplainResult(plan_text="", estimated_rows=50, planner_cost=-1)
    assert _plan_says_cheap(plan, cfg) is True


def test_sqlite_full_scan_sentinel_is_never_cheap(cfg):
    # The SQLite explainer uses 2**31 to signal a full scan.
    plan = ExplainResult(plan_text="", estimated_rows=2**31, planner_cost=-1)
    assert _plan_says_cheap(plan, cfg) is False


def test_sqlite_unknown_is_never_cheap(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=-1, planner_cost=-1)
    assert _plan_says_cheap(plan, cfg) is False


# ── _reconcile ───────────────────────────────────────────────────────────────

def test_cheap_plan_lowers_heuristic_score(cfg):
    """The headline behaviour: a LIKE '%x' on a tiny table is not a problem."""
    plan = ExplainResult(plan_text="", estimated_rows=200, planner_cost=12.0)
    issues = [issue("LEADING_WILDCARD_LIKE"), issue("SELECT_STAR")]
    score, source = _reconcile(40, 0, plan, issues, cfg)
    assert score == 0
    assert source is ScoreSource.PLAN_CHEAP


def test_cartesian_join_keeps_its_penalty_even_on_a_cheap_plan(cfg):
    """A cartesian product on an empty staging table is still a bug."""
    plan = ExplainResult(plan_text="", estimated_rows=0, planner_cost=1.0)
    issues = [issue("CARTESIAN_JOIN")]
    score, source = _reconcile(40, 0, plan, issues, cfg)
    assert score == 40, "structural defects must not be discounted"
    assert source is ScoreSource.HEURISTIC


def test_structural_floor_survives_but_cost_rules_are_dropped(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=10, planner_cost=2.0)
    issues = [issue("CARTESIAN_JOIN"), issue("SELECT_STAR"), issue("OR_IN_WHERE")]
    # heuristic = 40 + 15 + 5 = 60; only the cartesian penalty should remain.
    score, source = _reconcile(60, 0, plan, issues, cfg)
    assert score == 40
    assert source is ScoreSource.PLAN_CHEAP


def test_expensive_plan_still_raises_score(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=9_000_000, planner_cost=500_000.0)
    score, source = _reconcile(15, 80, plan, [issue("SELECT_STAR")], cfg)
    assert score == 80
    assert source is ScoreSource.PLAN


def test_heuristic_wins_when_plan_is_unavailable(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=-1, planner_cost=-1)
    score, source = _reconcile(55, 0, plan, [issue("LEADING_WILDCARD_LIKE")], cfg)
    assert score == 55
    assert source is ScoreSource.HEURISTIC


def test_cheap_plan_does_not_inflate_a_clean_query(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=1, planner_cost=0.5)
    score, source = _reconcile(0, 0, plan, [], cfg)
    assert score == 0
    assert source is ScoreSource.HEURISTIC


def test_cheap_cost_threshold_is_configurable(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=100, planner_cost=500.0)
    assert _plan_says_cheap(plan, cfg) is True
    cfg.thresholds.cheap_cost = 100.0
    assert _plan_says_cheap(plan, cfg) is False


# ── _score_from_explain ──────────────────────────────────────────────────────

def test_unknown_estimates_score_zero(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=-1, planner_cost=-1)
    assert _score_from_explain(plan, cfg) == 0


def test_explain_score_is_capped_at_100(cfg):
    plan = ExplainResult(plan_text="", estimated_rows=10**9, planner_cost=10**9)
    assert _score_from_explain(plan, cfg) == 80
"""
qcost.rules.ast_rules
~~~~~~~~~~~~~~~~~~~~~~~~~~
Heuristic rules implemented against the sqlglot AST.

This is the core advantage of Python over Go: sqlglot gives us a real
parse tree, so rules are precise — we inspect actual AST node types
rather than hoping regex matches line up with intent.

Each rule is a callable:
    rule(ast: sqlglot.Expression, dialect: str) -> list[Issue]

Rules are collected in RULES and run by the analyzer.
"""
from __future__ import annotations

from typing import Callable

import sqlglot
import sqlglot.expressions as exp

from qcost.models import CostTier, Issue

# Type alias for a rule function.
RuleFn = Callable[[exp.Expression, str], list[Issue]]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_issue(code: str, severity: CostTier, message: str, suggestion: str) -> Issue:
    return Issue(code=code, severity=severity, message=message, suggestion=suggestion)


def _has_where(node: exp.Expression) -> bool:
    return node.find(exp.Where) is not None


def _has_limit(node: exp.Expression) -> bool:
    return node.find(exp.Limit) is not None


def _select_columns(node: exp.Select) -> list[exp.Expression]:
    return list(node.selects)


# ── Rules ─────────────────────────────────────────────────────────────────────

def rule_select_star(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """SELECT * fetches all columns, including large BLOBs the caller may not need."""
    issues = []
    for select in ast.find_all(exp.Select):
        for col in select.selects:
            # Catches SELECT * (exp.Star) and SELECT t.* (Column whose child is a Star)
            is_star = (
                isinstance(col, exp.Star)
                or (isinstance(col, exp.Column) and isinstance(col.this, exp.Star))
                or col.find(exp.Star) is not None
            )
            if is_star:
                issues.append(_make_issue(
                    code       = "SELECT_STAR",
                    severity   = CostTier.MEDIUM,
                    message    = "SELECT * fetches all columns including unused/large ones",
                    suggestion = "List only the columns your application actually reads.",
                ))
                break  # one issue per SELECT node is enough
    return issues


def rule_full_table_scan(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """SELECT without WHERE or LIMIT will scan every row in the table."""
    issues = []
    for select in ast.find_all(exp.Select):
        # Only flag top-level or subquery SELECTs that touch a real table.
        if not select.find(exp.Table):
            continue
        if not _has_where(select) and not _has_limit(select):
            issues.append(_make_issue(
                code       = "FULL_TABLE_SCAN_NO_WHERE",
                severity   = CostTier.HIGH,
                message    = "SELECT with no WHERE or LIMIT will scan the entire table",
                suggestion = "Add a WHERE clause or LIMIT to restrict the result set.",
            ))
    return issues


def rule_cartesian_join(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    A JOIN (or implicit comma join) with no ON/USING condition produces a
    cartesian product — every row in A × every row in B.
    sqlglot normalises comma-joins to Cross joins, making this reliable.
    """
    issues = []
    for join in ast.find_all(exp.Join):
        is_cross    = join.args.get("kind") == "CROSS"
        has_on      = join.args.get("on") is not None
        has_using   = join.args.get("using") is not None

        if is_cross or (not has_on and not has_using):
            issues.append(_make_issue(
                code       = "CARTESIAN_JOIN",
                severity   = CostTier.CRITICAL,
                message    = "JOIN without ON/USING — likely a cartesian product",
                suggestion = "Add an explicit ON clause to restrict the join.",
            ))
    return issues


def rule_leading_wildcard_like(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    LIKE '%term' cannot use a B-tree index because the leading character
    is unknown.  sqlglot parses the pattern literal so we can inspect it
    directly rather than using fragile regex.
    """
    issues = []
    for like_node in ast.find_all(exp.Like):
        pattern = like_node.args.get("expression")
        if isinstance(pattern, exp.Literal) and str(pattern.this).startswith("%"):
            issues.append(_make_issue(
                code       = "LEADING_WILDCARD_LIKE",
                severity   = CostTier.HIGH,
                message    = f"LIKE pattern '{pattern.this}' starts with % — prevents index usage",
                suggestion = "Use a full-text search index, or restructure to avoid a leading wildcard.",
            ))
    return issues


def rule_function_on_column_in_where(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    Wrapping an indexed column in a function (WHERE LOWER(email) = ...)
    forces a full scan because the index is built on the raw value.
    sqlglot lets us check specifically whether the function argument is a
    Column node that appears inside a WHERE predicate.
    """
    OFFENDING_FUNCTIONS = {
        "LOWER", "UPPER", "DATE", "YEAR", "MONTH", "DAY",
        "TO_CHAR", "CAST", "COALESCE", "TRIM", "LENGTH",
        "SUBSTR", "SUBSTRING", "REPLACE",
    }
    issues = []
    for where in ast.find_all(exp.Where):
        for fn in where.find_all(exp.Anonymous, exp.Upper, exp.Lower,
                                  exp.Cast, exp.Coalesce, exp.TryCast):
            fn_name = (
                fn.name.upper()
                if hasattr(fn, "name") and fn.name
                else type(fn).__name__.upper()
            )
            if fn_name in OFFENDING_FUNCTIONS:
                # Check if any direct argument is a plain column reference.
                for arg in fn.args.values():
                    if isinstance(arg, exp.Column):
                        issues.append(_make_issue(
                            code       = "FUNCTION_ON_INDEXED_COLUMN",
                            severity   = CostTier.HIGH,
                            message    = f"{fn_name}() applied to column '{arg.name}' in WHERE prevents index usage",
                            suggestion = (
                                f"Use a functional index on {fn_name}({arg.name}), "
                                "or store the pre-transformed value in a separate column."
                            ),
                        ))
                        break
    return issues


def rule_subquery_in_where(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    A correlated subquery in WHERE may execute once per row of the outer query.
    sqlglot distinguishes Subquery nodes precisely.
    """
    issues = []
    for where in ast.find_all(exp.Where):
        for subq in where.find_all(exp.Subquery):
            # Only flag if the subquery references a column from the outer scope
            # (correlated).  For uncorrelated subqueries we still warn but softer.
            outer_tables = {
                t.name.lower()
                for t in ast.find_all(exp.Table)
                if t not in subq.find_all(exp.Table)
            }
            is_correlated = any(
                col.table and col.table.lower() in outer_tables
                for col in subq.find_all(exp.Column)
            )
            issues.append(_make_issue(
                code       = "SUBQUERY_IN_WHERE",
                severity   = CostTier.HIGH if is_correlated else CostTier.MEDIUM,
                message    = (
                    "Correlated subquery in WHERE may execute once per outer row"
                    if is_correlated else
                    "Subquery in WHERE — consider rewriting as a JOIN"
                ),
                suggestion = "Rewrite as a JOIN or use EXISTS with an indexed column.",
            ))
            break  # one issue per WHERE is enough
    return issues


def rule_order_without_limit(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """ORDER BY without LIMIT on a large table causes an expensive sort of the full result."""
    issues = []
    for select in ast.find_all(exp.Select):
        if select.find(exp.Order) and not _has_limit(select):
            issues.append(_make_issue(
                code       = "ORDER_BY_NO_LIMIT",
                severity   = CostTier.LOW,
                message    = "ORDER BY without LIMIT may sort the entire result set",
                suggestion = "Add LIMIT, or ensure the ORDER BY column(s) are indexed.",
            ))
    return issues


def rule_or_in_where(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    OR in WHERE can prevent the planner from using an index on either branch
    unless both columns are individually indexed (and even then the planner
    may choose a bitmap scan or full scan).
    """
    issues = []
    for where in ast.find_all(exp.Where):
        if where.find(exp.Or):
            issues.append(_make_issue(
                code       = "OR_IN_WHERE",
                severity   = CostTier.LOW,
                message    = "OR in WHERE can prevent index usage depending on column coverage",
                suggestion = "Consider rewriting as UNION ALL, or verify both branches are indexed.",
            ))
    return issues


def rule_implicit_type_cast(ast: exp.Expression, dialect: str) -> list[Issue]:
    """
    Comparing a typed column to a literal of a different type forces an
    implicit cast on every row.  e.g. WHERE int_col = '42' (string literal).
    sqlglot's type inference (available for Postgres/MySQL) makes this detectable.
    Only runs for postgres and mysql dialects.
    """
    if dialect not in ("postgres", "mysql"):
        return []

    issues = []
    # Annotate types so we can compare.
    try:
        annotated = sqlglot.parse_one(ast.sql(dialect=dialect), dialect=dialect)
        annotated = annotated  # type-annotated AST
    except Exception:
        return []

    for eq in ast.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if not isinstance(left, exp.Column):
            continue
        # If the right side is a string literal but the column name looks numeric
        # (common pattern: id = '123'), flag it.
        if isinstance(right, exp.Literal) and right.is_string:
            col_name = left.name.lower()
            if any(s in col_name for s in ("id", "count", "num", "qty", "amount", "price")):
                issues.append(_make_issue(
                    code       = "IMPLICIT_TYPE_CAST",
                    severity   = CostTier.MEDIUM,
                    message    = f"String literal compared to likely-numeric column '{left.name}' — forces implicit cast per row",
                    suggestion = f"Use a numeric literal: WHERE {left.name} = 42 (not '42').",
                ))
    return issues


def rule_select_in_loop_hint(ast: exp.Expression, _dialect: str) -> list[Issue]:
    """
    sqlglot can't see Python/Go loop context, but multiple identical-structure
    SELECTs in the same file (detected by the extractor) may indicate N+1.
    This rule fires when the same table is SELECTed without any join or CTE
    and the query is a single-row lookup — a common N+1 signature.
    """
    # Placeholder — N+1 detection is implemented at the file level in analyzer.py
    # where we can compare all queries in the file against each other.
    return []


# ── Rule registry ─────────────────────────────────────────────────────────────

RULES: list[RuleFn] = [
    rule_select_star,
    rule_full_table_scan,
    rule_cartesian_join,
    rule_leading_wildcard_like,
    rule_function_on_column_in_where,
    rule_subquery_in_where,
    rule_order_without_limit,
    rule_or_in_where,
    rule_implicit_type_cast,
]

# Score penalty per issue code (0–100 scale, additive, capped at 100).
SCORE_MAP: dict[str, int] = {
    "CARTESIAN_JOIN":            40,
    "MISSING_JOIN_CONDITION":    35,
    "FULL_TABLE_SCAN_NO_WHERE":  30,
    "LEADING_WILDCARD_LIKE":     25,
    "FUNCTION_ON_INDEXED_COLUMN":20,
    "SUBQUERY_IN_WHERE":         15,
    "SELECT_STAR":               15,
    "ORDER_BY_NO_LIMIT":          8,
    "IMPLICIT_TYPE_CAST":        10,
    "OR_IN_WHERE":                5,
}
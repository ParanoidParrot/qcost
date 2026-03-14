"""
Tests for qcost.rules and qcost.analyzer.

Run with:  pytest tests/ -v
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import sqlglot

from qcost.rules.ast_rules import (
    rule_select_star,
    rule_full_table_scan,
    rule_cartesian_join,
    rule_leading_wildcard_like,
    rule_function_on_column_in_where,
    rule_subquery_in_where,
    rule_order_without_limit,
    rule_or_in_where,
)
from qcost.models import CostTier
from qcost.analyzer import run_sql, build_report
from qcost.config import Config
from qcost.extractors.source import from_file


def parse(sql: str, dialect: str = "postgres"):
    return sqlglot.parse_one(sql, dialect=dialect)


# ── Individual rule tests ─────────────────────────────────────────────────────

class TestSelectStar:
    def test_fires_on_star(self):
        issues = rule_select_star(parse("SELECT * FROM users"), "postgres")
        assert any(i.code == "SELECT_STAR" for i in issues)

    def test_silent_on_explicit_columns(self):
        issues = rule_select_star(parse("SELECT id, email FROM users"), "postgres")
        assert not issues

    def test_fires_on_table_dot_star(self):
        issues = rule_select_star(parse("SELECT u.* FROM users u"), "postgres")
        assert any(i.code == "SELECT_STAR" for i in issues)


class TestFullTableScan:
    def test_fires_without_where_or_limit(self):
        issues = rule_full_table_scan(parse("SELECT id FROM users"), "postgres")
        assert any(i.code == "FULL_TABLE_SCAN_NO_WHERE" for i in issues)

    def test_silent_with_where(self):
        issues = rule_full_table_scan(
            parse("SELECT id FROM users WHERE active = true"), "postgres"
        )
        assert not any(i.code == "FULL_TABLE_SCAN_NO_WHERE" for i in issues)

    def test_silent_with_limit(self):
        issues = rule_full_table_scan(
            parse("SELECT id FROM users LIMIT 10"), "postgres"
        )
        assert not any(i.code == "FULL_TABLE_SCAN_NO_WHERE" for i in issues)


class TestCartesianJoin:
    def test_fires_on_join_without_on(self):
        issues = rule_cartesian_join(
            parse("SELECT * FROM users u, orders o"), "postgres"
        )
        assert any(i.code == "CARTESIAN_JOIN" for i in issues)

    def test_silent_on_proper_join(self):
        issues = rule_cartesian_join(
            parse("SELECT * FROM users u JOIN orders o ON o.user_id = u.id"), "postgres"
        )
        assert not any(i.code == "CARTESIAN_JOIN" for i in issues)


class TestLeadingWildcard:
    def test_fires_on_leading_percent(self):
        issues = rule_leading_wildcard_like(
            parse("SELECT id FROM users WHERE email LIKE '%@gmail.com'"), "postgres"
        )
        assert any(i.code == "LEADING_WILDCARD_LIKE" for i in issues)

    def test_silent_on_trailing_percent(self):
        issues = rule_leading_wildcard_like(
            parse("SELECT id FROM users WHERE name LIKE 'alice%'"), "postgres"
        )
        assert not any(i.code == "LEADING_WILDCARD_LIKE" for i in issues)


class TestFunctionOnColumn:
    def test_fires_on_lower_in_where(self):
        issues = rule_function_on_column_in_where(
            parse("SELECT id FROM users WHERE LOWER(email) = 'alice@example.com'"),
            "postgres",
        )
        assert any(i.code == "FUNCTION_ON_INDEXED_COLUMN" for i in issues)

    def test_silent_when_function_not_on_column(self):
        issues = rule_function_on_column_in_where(
            parse("SELECT id FROM users WHERE email = LOWER('ALICE@EXAMPLE.COM')"),
            "postgres",
        )
        assert not any(i.code == "FUNCTION_ON_INDEXED_COLUMN" for i in issues)


class TestSubqueryInWhere:
    def test_fires_on_subquery_in_where(self):
        issues = rule_subquery_in_where(
            parse(
                "SELECT * FROM orders "
                "WHERE user_id IN (SELECT id FROM users WHERE active = true)"
            ),
            "postgres",
        )
        assert any(i.code == "SUBQUERY_IN_WHERE" for i in issues)


class TestOrderWithoutLimit:
    def test_fires_without_limit(self):
        issues = rule_order_without_limit(
            parse("SELECT id FROM events ORDER BY created_at DESC"), "postgres"
        )
        assert any(i.code == "ORDER_BY_NO_LIMIT" for i in issues)

    def test_silent_with_limit(self):
        issues = rule_order_without_limit(
            parse("SELECT id FROM events ORDER BY created_at DESC LIMIT 20"), "postgres"
        )
        assert not any(i.code == "ORDER_BY_NO_LIMIT" for i in issues)


class TestOrInWhere:
    def test_fires_on_or(self):
        issues = rule_or_in_where(
            parse("SELECT id FROM products WHERE category = 'shoes' OR category = 'bags'"),
            "postgres",
        )
        assert any(i.code == "OR_IN_WHERE" for i in issues)

    def test_silent_without_or(self):
        issues = rule_or_in_where(
            parse("SELECT id FROM products WHERE category = 'shoes'"),
            "postgres",
        )
        assert not any(i.code == "OR_IN_WHERE" for i in issues)


# ── Analyzer integration tests ────────────────────────────────────────────────

class TestAnalyzer:
    def _cfg(self) -> Config:
        return Config()

    def test_clean_query_scores_zero(self):
        result = run_sql(
            "SELECT id, email FROM users WHERE id = 1 LIMIT 1",
            "<test>", self._cfg()
        )
        assert result.score == 0
        assert result.tier == CostTier.LOW
        assert result.issues == []

    def test_bad_query_scores_high(self):
        result = run_sql("SELECT * FROM users", "<test>", self._cfg())
        assert result.score >= 40
        assert result.tier in (CostTier.HIGH, CostTier.CRITICAL, CostTier.MEDIUM)

    def test_multiple_issues_add_up(self):
        result = run_sql(
            "SELECT * FROM users WHERE email LIKE '%@example.com'",
            "<test>", self._cfg()
        )
        assert result.score >= 40
        assert len(result.issues) >= 2

    def test_build_report_pass(self):
        cfg = self._cfg()
        results = [
            run_sql("SELECT id FROM users WHERE id = 1 LIMIT 1", "<test>", cfg),
        ]
        report = build_report(results, cfg)
        assert report.pass_gate is True

    def test_build_report_fail(self):
        cfg = self._cfg()
        cfg.thresholds.fail_score = 30
        results = [run_sql("SELECT * FROM users", "<test>", cfg)]
        report = build_report(results, cfg)
        assert report.pass_gate is False


# ── Extractor tests ───────────────────────────────────────────────────────────

class TestExtractor:
    def test_sql_file(self, tmp_path: Path):
        f = tmp_path / "test.sql"
        f.write_text(textwrap.dedent("""\
            SELECT * FROM users;
            SELECT id FROM accounts WHERE id = 1;
            DELETE FROM sessions WHERE expires_at < NOW();
        """))
        queries = from_file(f)
        assert len(queries) == 3

    def test_python_file(self, tmp_path: Path):
        f = tmp_path / "repo.py"
        f.write_text(textwrap.dedent("""\
            def get_user(db, user_id):
                return db.execute("SELECT id, name FROM users WHERE id = %s", (user_id,))
        """))
        queries = from_file(f)
        assert len(queries) == 1
        assert "SELECT" in queries[0].sql

    def test_go_file(self, tmp_path: Path):
        f = tmp_path / "repo.go"
        f.write_text(textwrap.dedent("""\
            func GetUser(ctx context.Context, db *sql.DB, id int) {
                db.QueryContext(ctx, "SELECT id, name FROM users WHERE id = $1", id)
            }
        """))
        queries = from_file(f)
        assert len(queries) == 1

    def test_ignores_ddl(self, tmp_path: Path):
        f = tmp_path / "migration.sql"
        f.write_text("CREATE TABLE users (id SERIAL PRIMARY KEY);\n")
        queries = from_file(f)
        assert len(queries) == 0
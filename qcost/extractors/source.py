"""
qcost.extractors.source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pulls SQL query strings out of source files.

For .sql files  → split on semicolons, keep DML statements.
For .py/.go/.ts → regex over common ORM / driver call patterns.

Returns ExtractedQuery objects with the SQL string and its source location.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ── Data type ─────────────────────────────────────────────────────────────────

@dataclass
class ExtractedQuery:
    sql:  str
    file: str
    line: int   # 1-based


# ── DML prefixes we want to analyse ──────────────────────────────────────────

_DML_PREFIXES = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


def _is_meaningful(sql: str) -> bool:
    s = sql.strip().upper()
    return len(s) >= 10 and any(s.startswith(p) for p in _DML_PREFIXES)


# ── Per-language patterns ─────────────────────────────────────────────────────

# Python: cursor.execute("..."), session.execute(text("...")), db.query("...")
_PY_RE = re.compile(
    r'(?:execute|executemany|query|raw)\s*\(\s*(?:text\s*\(\s*)?'
    r'(?:f?["\']([^"\']{10,})["\']|f?"""(.*?)""")',
    re.IGNORECASE | re.DOTALL,
)

# Go: db.Query("..."), db.QueryContext(ctx, "..."), db.Exec("..."), db.Raw("...")
_GO_RE = re.compile(
    r'(?:Query|Exec|QueryContext|ExecContext|QueryRow|QueryRowContext|Raw|Find|Where)'
    r'(?:Context)?\s*\(\s*(?:ctx,\s*)?[`"]([^`"]{10,})[`"]',
    re.IGNORECASE,
)

# TypeScript/JS: db.query("..."), prisma.$queryRaw`...`, knex.raw("...")
_TS_RE = re.compile(
    r'(?:query|execute|\$queryRaw|\$executeRaw|raw)\s*\(?[\s`\'"]([^`\'"]{10,})[`\'"]',
    re.IGNORECASE,
)

_LANG_PATTERNS: dict[str, re.Pattern[str]] = {
    ".py":  _PY_RE,
    ".go":  _GO_RE,
    ".ts":  _TS_RE,
    ".tsx": _TS_RE,
    ".js":  _TS_RE,
    ".jsx": _TS_RE,
}

# ── Public API ────────────────────────────────────────────────────────────────

def from_file(path: str | Path) -> list[ExtractedQuery]:
    """Extract all SQL queries from *path*."""
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".sql":
        return _from_sql_file(p)

    pattern = _LANG_PATTERNS.get(suffix)
    if pattern:
        return _from_source_file(p, pattern)

    return []


def _from_sql_file(path: Path) -> list[ExtractedQuery]:
    text = path.read_text(errors="replace")
    results: list[ExtractedQuery] = []
    buf: list[str] = []
    start_line = 1
    current_line = 1

    for line in text.splitlines():
        stripped = line.strip()

        # Skip pure comment lines.
        if stripped.startswith("--") or stripped.startswith("/*"):
            current_line += 1
            continue

        buf.append(line)

        if stripped.endswith(";"):
            sql = "\n".join(buf).strip().rstrip(";").strip()
            if _is_meaningful(sql):
                results.append(ExtractedQuery(sql=sql, file=str(path), line=start_line))
            buf = []
            start_line = current_line + 1

        current_line += 1

    # Flush trailing statement without semicolon.
    if sql := "\n".join(buf).strip():
        if _is_meaningful(sql):
            results.append(ExtractedQuery(sql=sql, file=str(path), line=start_line))

    return results


def _from_source_file(path: Path, pattern: re.Pattern[str]) -> list[ExtractedQuery]:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    results: list[ExtractedQuery] = []

    for i, line in enumerate(lines, start=1):
        for match in pattern.finditer(line):
            # The pattern may have two capture groups (single/triple quoted).
            sql = next((g for g in match.groups() if g), None)
            if sql and _is_meaningful(sql.strip()):
                results.append(ExtractedQuery(
                    sql=sql.strip(),
                    file=str(path),
                    line=i,
                ))

    return results
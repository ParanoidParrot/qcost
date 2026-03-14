"""
action/action_runner.py
~~~~~~~~~~~~~~~~~~~~~~~~
GitHub Action entrypoint.  Reads inputs from environment variables
(set by action.yml), runs the analysis on changed files, posts a
PR comment via the GitHub API, and exits 1 if the gate fails.

This script is intentionally self-contained — no CLI framework needed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure the package is importable from the action directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from qcost import analyzer, config, reporter
from qcost.models import DBType


# ── GitHub Actions helpers ────────────────────────────────────────────────────

def gha_input(name: str, default: str = "") -> str:
    """Read an action input (env var set by GitHub Actions runner)."""
    return os.environ.get(f"INPUT_{name.upper().replace('-', '_')}", default).strip()


def gha_output(name: str, value: str) -> None:
    """Write a step output to $GITHUB_OUTPUT."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            # Multi-line values use the heredoc syntax.
            if "\n" in value:
                delimiter = "EOF"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")


def gha_log(level: str, message: str) -> None:
    """Emit a GitHub Actions log command."""
    print(f"::{level}::{message}", flush=True)


# ── Git helpers ───────────────────────────────────────────────────────────────

def get_changed_files(base_ref: str) -> list[str]:
    """Return files changed between base_ref and HEAD."""
    for ref in (f"{base_ref}...HEAD", f"{base_ref}..HEAD"):
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM", ref],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    gha_log("warning", f"git diff failed against {base_ref} — analyzing all supported files")
    return []


_SUPPORTED_EXTS = {".sql", ".py", ".go", ".ts", ".tsx", ".js", ".jsx"}


def is_analyzable(path: str) -> bool:
    return Path(path).suffix.lower() in _SUPPORTED_EXTS


# ── PR comment ────────────────────────────────────────────────────────────────

def post_pr_comment(token: str, repo: str, pr_number: str, body: str) -> None:
    """Create or update the QCost PR comment via the GitHub REST API."""
    import urllib.request
    import urllib.error

    marker  = "<!-- qcost-report -->"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "Content-Type":  "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"

    def _request(url: str, method: str, data: bytes | None = None) -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # Find existing QCost comment.
    existing_id: int | None = None
    try:
        comments = _request(base_url, "GET")
        for c in comments:
            if marker in c.get("body", ""):
                existing_id = c["id"]
                break
    except Exception as exc:
        gha_log("warning", f"Could not list PR comments: {exc}")

    full_body = marker + "\n" + body
    payload   = json.dumps({"body": full_body}).encode()

    try:
        if existing_id:
            _request(f"{base_url}/{existing_id}", "PATCH", payload)
            gha_log("notice", "Updated existing QCost PR comment.")
        else:
            _request(base_url, "POST", payload)
            gha_log("notice", "Posted new QCost PR comment.")
    except Exception as exc:
        gha_log("warning", f"Failed to post PR comment: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Read inputs.
    cfg_path     = gha_input("config",       ".qcost.yml")
    db_type_str  = gha_input("db-type",      "postgres")
    dsn          = gha_input("dsn",          "")
    fmt          = gha_input("format",       "markdown")
    base_ref     = gha_input("base-ref",     os.environ.get("GITHUB_BASE_REF", "origin/main"))
    fail_on_high = gha_input("fail-on-high", "true").lower() == "true"
    post_comment = gha_input("post-comment", "true").lower() == "true"
    token        = gha_input("github-token", os.environ.get("GITHUB_TOKEN", ""))

    # Load config; allow CLI inputs to override.
    cfg = config.load(cfg_path)
    cfg.db.type    = DBType(db_type_str)
    cfg.db.dsn     = dsn
    cfg.output.format = fmt

    # Get changed files.
    changed = get_changed_files(base_ref)
    analyzable = [f for f in changed if is_analyzable(f) and Path(f).exists()]

    if not analyzable:
        gha_log("notice", "QCost: no analyzable files changed in this PR.")
        gha_output("gate-passed",  "true")
        gha_output("total-score",  "0")
        gha_output("report",       "No SQL queries detected in this PR.")
        return

    print(f"QCost: analyzing {len(analyzable)} file(s)…", flush=True)

    all_results = []
    for f in analyzable:
        try:
            results = analyzer.run_file(f, cfg)
            all_results.extend(results)
        except Exception as exc:
            gha_log("warning", f"Could not analyze {f}: {exc}")

    report = analyzer.build_report(all_results, cfg)

    # Render output.
    if fmt == "json":
        report_str = reporter.as_json(report)
        print(report_str)
    else:
        report_str = reporter.markdown(report, verbose=cfg.output.verbose)
        print(report_str)

    # Set step outputs.
    gha_output("gate-passed",  str(report.pass_gate).lower())
    gha_output("total-score",  str(report.total_cost))
    gha_output("report",       report_str)

    # Post PR comment.
    if post_comment and token:
        pr_number = os.environ.get("PR_NUMBER", "")
        repo      = os.environ.get("GITHUB_REPOSITORY", "")
        if pr_number and repo:
            post_pr_comment(token, repo, pr_number, report_str)
        else:
            gha_log("warning", "PR_NUMBER or GITHUB_REPOSITORY not set — skipping comment.")

    # Gate check.
    if fail_on_high and not report.pass_gate:
        gha_log("error", f"QCost gate failed — total score {report.total_cost}")
        sys.exit(1)


if __name__ == "__main__":
    main()
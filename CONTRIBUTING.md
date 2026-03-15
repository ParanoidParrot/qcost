# Contributing to qcost

Thanks for your interest in contributing. qcost is a focused tool — contributions
that keep it sharp and well-scoped are most welcome.

## Setup

```bash
git clone https://github.com/ParanoidParrot/qcost
cd qcost

# Create a dedicated environment
conda create -n qcost python=3.12 -y
conda activate qcost

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Verify everything works
pytest tests/ -v        # 26 tests should pass
ruff check qcost tests  # should be clean
```

## Adding a new rule

All rules live in `qcost/rules/ast_rules.py`. Each rule is a function:

```python
def rule_your_rule_name(ast: exp.Expression, dialect: str) -> list[Issue]:
    """One-line description of what this detects."""
    issues = []
    # walk the AST using sqlglot expressions
    for node in ast.find_all(exp.SomeNode):
        if some_condition(node):
            issues.append(_make_issue(
                code       = "YOUR_RULE_CODE",
                severity   = CostTier.HIGH,
                message    = "What's wrong",
                suggestion = "How to fix it",
            ))
    return issues
```

Then:
1. Add it to the `RULES` list at the bottom of `ast_rules.py`
2. Add its score penalty to `SCORE_MAP`
3. Add a test class in `tests/test_rules.py` with at least one positive and one negative case

## Running a specific test

```bash
pytest tests/test_rules.py::TestYourRule -v
```

## Useful sqlglot references

- [sqlglot expression types](https://github.com/tobymao/sqlglot/blob/main/sqlglot/expressions.py)
- [sqlglot playground](https://sqlglot.com/sqlglot.html) — paste SQL, see the AST

## Submitting a PR

- Keep PRs focused — one rule or one fix per PR
- All tests must pass and ruff must be clean
- Add or update tests for any changed behaviour
- Update `SCORE_MAP` if adding a new rule code
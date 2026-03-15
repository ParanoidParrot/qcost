"""
qcost.config
~~~~~~~~~~~~~~~~~
Loads .qcost.yml and exposes a typed Config object.
Falls back to sane defaults when the file is absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from qcost.models import DBType


@dataclass
class DBConfig:
    type: DBType = DBType.POSTGRES
    dsn:  str    = ""          # empty = heuristic mode only


@dataclass
class Thresholds:
    fail_score: int  = 75      # gate fails at this score
    warn_score: int  = 40      # warning only below fail_score
    max_rows:   int  = 500_000 # flag result sets larger than this


@dataclass
class ScanConfig:
    include: list[str] = field(default_factory=lambda: [
        "**/*.sql",
        "**/migrations/**/*.py",
        "**/migrations/**/*.go",
        "**/migrations/**/*.ts",
        "**/db/**/*.py",
        "**/repository/**/*.py",
    ])
    exclude: list[str] = field(default_factory=lambda: [
        "vendor/**",
        "node_modules/**",
        "**/*_test.py",
        "**/test_*.py",
        "**/testdata/**",
    ])


@dataclass
class OutputConfig:
    format:  str  = "text"   # text | json | markdown
    verbose: bool = False


@dataclass
class Config:
    db:         DBConfig     = field(default_factory=DBConfig)
    thresholds: Thresholds   = field(default_factory=Thresholds)
    scan:       ScanConfig   = field(default_factory=ScanConfig)
    output:     OutputConfig = field(default_factory=OutputConfig)


def load(path: str | Path = ".qcost.yml") -> Config:
    """
    Read config from *path*.  If the file doesn't exist, return defaults.
    CLI flags should be applied *after* calling this, overriding fields directly.
    """
    cfg = Config()
    p = Path(path)

    if not p.exists():
        return cfg

    with p.open() as f:
        raw: dict = yaml.safe_load(f) or {}

    # db
    if db := raw.get("db"):
        if t := db.get("type"):
            cfg.db.type = DBType(t)
        if dsn := db.get("dsn"):
            cfg.db.dsn = dsn

    # thresholds
    if th := raw.get("thresholds"):
        if v := th.get("fail_score"): cfg.thresholds.fail_score = int(v)
        if v := th.get("warn_score"): cfg.thresholds.warn_score = int(v)
        if v := th.get("max_rows"):   cfg.thresholds.max_rows   = int(v)

    # scan
    if sc := raw.get("scan"):
        if v := sc.get("include"): cfg.scan.include = v
        if v := sc.get("exclude"): cfg.scan.exclude = v

    # output
    if out := raw.get("output"):
        if v := out.get("format"):  cfg.output.format  = v
        if v := out.get("verbose"): cfg.output.verbose = bool(v)

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.thresholds.warn_score >= cfg.thresholds.fail_score:
        raise ValueError(
            f"thresholds.warn_score ({cfg.thresholds.warn_score}) must be "
            f"less than fail_score ({cfg.thresholds.fail_score})"
        )
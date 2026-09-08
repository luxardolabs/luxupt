#!/usr/bin/env python3
"""Shared helpers for the commit-ledger hooks (record / stop-guard / clear)."""

import json
import os
import subprocess
from pathlib import Path


def project_dir() -> Path:
    d = os.environ.get("CLAUDE_PROJECT_DIR")
    if d:
        return Path(d)
    try:
        return Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except Exception:
        return Path.cwd()


def ledger_path() -> Path:
    return project_dir() / ".claude" / ".luxpm-ledger.json"


def read_ledger() -> list:
    try:
        return json.loads(ledger_path().read_text(encoding="utf-8"))
    except Exception:
        return []


def write_ledger(entries: list) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(project_dir()), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""

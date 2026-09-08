#!/usr/bin/env python3
"""Stamp proof that hooks actually EXECUTED — not merely that they are installed.

A hook set can be present on disk, correctly wired in settings.json, and still never run:
disabled at the enterprise/user level, or the harness never loaded them. That is a hollow
green of the worst kind — the repo looks protected and isn't (it is how a fleet ran for weeks
with its hooks switched off). No static check can see it, because the evidence of absence is
an absence.

So the hooks prove themselves: this stamps `.claude/.hooks-heartbeat` (gitignored) with the
timestamp and the luxarch pin on every tool call. `repo.hooks_wired` reads it — a fresh/stale
heartbeat makes the rule INERT ("wired, but I cannot confirm they ran"), never a bare pass.

PreToolUse(*): touch the heartbeat. Never blocks, never errors out.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        if not root:
            root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        p = Path(root) / ".claude" / ".hooks-heartbeat"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"at": int(time.time()), "pid": os.getpid()}), encoding="utf-8"
        )
    except Exception:
        pass  # a heartbeat must never break a tool call
    return 0


sys.exit(main())

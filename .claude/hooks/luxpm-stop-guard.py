#!/usr/bin/env python3
"""Refuse to end the turn while committed work is unlogged in LuxPM.

The enforcement half of the ledger loop: instructions slip, a Stop hook does not. Logging to
LuxPM clears the ledger (luxpm-clear-ledger.py) and the next Stop passes.
Emergency override: delete .claude/.luxpm-ledger.json.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ledger import read_ledger  # noqa: E402


def _already_blocked() -> bool:
    """True when this Stop hook has ALREADY blocked once this turn.

    Claude Code sets `stop_hook_active` on the re-entry after a Stop hook blocks. Honouring it is
    what makes a Stop guard a reminder rather than a trap: it gets one turn to put the obligation
    in front of the agent, then stands down. Without it the hook loops until the harness
    force-overrides — nine consecutive turns, observed live — and an agent that legitimately
    cannot comply (a push awaiting authorization) has no way out at all."""
    try:
        return bool(json.load(sys.stdin).get("stop_hook_active"))
    except Exception:
        return False


def main() -> int:
    if _already_blocked():
        return 0
    pending = read_ledger()
    if not pending:
        return 0
    lines = "\n".join(f"  {e['sha']}  {e['subject'][:70]}" for e in pending)
    print(
        f"{len(pending)} commit(s) are not logged in LuxPM:\n{lines}\n"
        "Log them (luxpm_create_activity / luxpm_log_work, or close the issue) before "
        "finishing — work that isn't in the tracker is invisible.",
        file=sys.stderr,
    )
    return 2


sys.exit(main())

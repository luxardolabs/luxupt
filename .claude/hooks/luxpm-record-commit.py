#!/usr/bin/env python3
"""Record each new commit in a local ledger so it can't be forgotten in LuxPM.

House rule: ALL work is tracked in LuxPM. An instruction alone slips mid-task, so this makes
it mechanical. Pairs with luxpm-stop-guard.py (refuses to end the turn while the ledger has
unlogged commits) and luxpm-clear-ledger.py (empties it when you log to LuxPM).

PostToolUse(Bash): when a `git commit` produced a NEW HEAD, append it and surface a reminder.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ledger import git, read_ledger, write_ledger  # noqa: E402


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return 0
    sha, subject = git("rev-parse", "--short", "HEAD"), git("log", "-1", "--pretty=%s")
    if not sha:
        return 0
    entries = read_ledger()
    if any(e.get("sha") == sha for e in entries):
        return 0
    issue = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", subject)
    entries.append(
        {"sha": sha, "subject": subject, "issue": issue.group(1) if issue else None}
    )
    write_ledger(entries)
    print(
        f"LuxPM: recorded {sha} ({subject[:60]}). Log the work to LuxPM before ending the "
        f"turn — {len(entries)} commit(s) pending.",
        file=sys.stderr,
    )
    return 0


sys.exit(main())

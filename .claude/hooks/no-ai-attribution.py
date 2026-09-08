#!/usr/bin/env python3
"""Deny commits / PRs carrying AI attribution.

House rule (CLAUDE.md, persistent): no `Co-Authored-By: Claude`, no "Generated with Claude
Code" in any commit or PR. The harness nudges for it, so this makes the rule mechanical.
A human Co-Authored-By trailer (not Claude/Anthropic) is fine and passes through.

PreToolUse(Bash): deny a git commit / gh pr create|edit whose text carries AI attribution.
"""

import json
import re
import sys

AUTHORING = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+(?:create|edit)\b", re.IGNORECASE)
ATTRIB = re.compile(
    r"(?i)co-authored-by:\s*[^\n]*(?:claude|anthropic|noreply@anthropic)"
    r"|generated\s+with\s+\[?claude"
    r"|\U0001F916\s*generated"
)


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("tool_name") != "Bash":
        return 0
    # Deliberately NOT masked: a commit message IS the quoted payload, so blanking quoted spans
    # would blind this hook entirely. It is already scoped to git-commit / gh-pr commands, so
    # prose about attribution elsewhere cannot trip it.
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    if not AUTHORING.search(cmd) or not ATTRIB.search(cmd):
        return 0
    print(
        "DENIED: no AI attribution in commits or PRs.\n"
        "Remove the Co-Authored-By / 'Generated with Claude Code' line and commit again. "
        "(A human co-author trailer is fine.)",
        file=sys.stderr,
    )
    return 2


sys.exit(main())

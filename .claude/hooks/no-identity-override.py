#!/usr/bin/env python3
"""Deny `git -c user.email=` / `-c user.name=` — commit as the fleet identity, always.

CLAUDE.md non-negotiable: commit as `luxardolabs` via the GLOBAL git config (the GitHub
noreply address). Passing a real email on the command line EXPOSES it in the commit and
GitHub blocks the push — and the fix is a rewrite. Documented everywhere, enforced nowhere
until now.

PreToolUse(Bash): deny an identity override on a git command.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cmdtext import command_text  # noqa: E402  # sibling helper, loaded by path

OVERRIDE = re.compile(
    r"\bgit\b[^\n]*-c\s*(?:user\.email|user\.name)\s*=", re.IGNORECASE
)


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("tool_name") != "Bash":
        return 0
    if not OVERRIDE.search((ev.get("tool_input") or {}).get("command") or ""):
        return 0
    print(
        "DENIED: never override the git identity with `-c user.email=` / `-c user.name=`.\n"
        "Commit with a plain `git commit` — the GLOBAL config already carries the fleet "
        "identity (luxardolabs + the GitHub noreply address). Passing a real email exposes it "
        "in the commit and GitHub blocks the push.",
        file=sys.stderr,
    )
    return 2


sys.exit(main())

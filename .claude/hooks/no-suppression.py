#!/usr/bin/env python3
"""Deny a BARE lint/type suppression — evidence is required, prohibition is not the fleet idiom.

Fleet rule: when a guard fires you fix the code or escalate; you never silence the check
(FLEET-AGENT-CONDUCT-STANDARD #7). But everywhere else the fleet demands EVIDENCE rather than
forbidding outright — the `# <slug>: <reason>` site waiver, `[allowlist].ruff` with an `evidence`
field, `[rules.deferred]` with a written line. A blanket deny was stricter than the standard and
offered no honest way through, which is the same defect LUXSIGNAL-20 found in unpushed-stop-guard.

So a suppression carrying a REASON on the same line passes (the gate still adjudicates it); a bare
one is denied. Found by dogfooding: the blanket version blocked edits to this very hook set, because
a hook legitimately needs a suppression for its import shape — and then blocked the fix to itself.

PreToolUse(Edit|Write): deny when the edit adds a suppression with no reason after it.
"""

import json
import re
import sys

# A suppression marker, its optional rule code, then whatever the author wrote after it.
SUPPRESSION = re.compile(
    r"#\s*(?:noqa|type:\s*ignore|ruff:\s*noqa|mypy:\s*ignore|pylint:\s*disable|nosec)"
    r"(?::?\s*\[?[\w, -]*\]?)?(?P<tail>[^\n]*)"
)
# A reason is prose after the marker/code: a second `#` comment, or a dash/colon lead-in.
REASON = re.compile(r"#\s*\S|[-—:]\s*\S")


def _bare(text: str) -> int:
    """How many suppressions in `text` carry NO written reason."""
    return sum(
        1
        for m in SUPPRESSION.finditer(text or "")
        if not REASON.search(m.group("tail") or "")
    )


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    ti = ev.get("tool_input") or {}
    old = ti.get("old_string") or ""
    new = ti.get("new_string") or ti.get("content") or ""
    if _bare(new) <= _bare(old):
        return 0
    print(
        "DENIED: this edit adds a BARE lint/type suppression.\n"
        "Fix the underlying code, or escalate the rule as wrong — a silenced check is how a real "
        "defect ships green. If the suppression IS warranted, write the reason on the same line "
        "(that is the fleet's evidence idiom, and it passes here); for a rule-level false positive "
        "the OWNER carves it out in [allowlist].ruff with code+file+match+evidence.",
        file=sys.stderr,
    )
    return 2


sys.exit(main())

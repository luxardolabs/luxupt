#!/usr/bin/env python3
"""Shared: the EXECUTABLE part of a shell command, with data spans masked out.

luxarch's own line-regex rules run through `core._masked_lines`, which blanks string and comment
spans so an example inside a docstring cannot trip a rule. A Bash hook needs the same doctrine: a
heredoc body and a quoted literal are DATA the command carries, not the command being run. Without
it the hooks matched raw text, so writing a test or a doc ABOUT a dangerous pattern read as
executing it — that blocked routine work, and then blocked their own repair.

Masked (replaced by spaces so offsets and line numbers survive):
  * heredoc bodies -- <<EOF ... EOF, <<'EOF' ... EOF, <<-EOF
  * single-quoted spans
  * double-quoted spans
"""

import re

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1(.*?)^\s*\2\s*$", re.S | re.M)
_SQ = re.compile(r"'[^']*'")
_DQ = re.compile(r'"[^"]*"')


def _blank(m):
    return "".join("\n" if c == "\n" else " " for c in m.group(0))


def command_text(cmd: str) -> str:
    """`cmd` with heredoc bodies and quoted literals blanked -- what is actually EXECUTED."""
    if not cmd:
        return ""
    out = _HEREDOC.sub(_blank, cmd)
    out = _SQ.sub(_blank, out)
    return _DQ.sub(_blank, out)

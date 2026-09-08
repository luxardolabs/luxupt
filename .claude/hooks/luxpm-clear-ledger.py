#!/usr/bin/env python3
"""Clear the commit ledger once the work is logged to LuxPM.

PostToolUse on the LuxPM logging tools: treat the log as covering the pending commits and
empty the ledger, so the Stop guard lets the turn end.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ledger import read_ledger, write_ledger  # noqa: E402


def main() -> int:
    if read_ledger():
        write_ledger([])
        print("LuxPM: work logged — commit ledger cleared.", file=sys.stderr)
    return 0


sys.exit(main())

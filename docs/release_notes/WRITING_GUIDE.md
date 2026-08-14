# Release Notes Writing Guide

The **customer-facing** release notes — what a LuxUPT user reads to know what changed. One markdown file per release, named by version (CalVer `YYYY.0M.MICRO`, e.g. `2026.08.1.md`), stamped with its release date. Plain docs — not served by the app.

For the internal, technical change log, see `docs/change_logs/`.

## Audience

People who run LuxUPT — capture snapshots from their UniFi Protect cameras, watch the timelapse videos, tune the scheduler. NOT developers, NOT fleet operators.

## The test

For every line item, ask: **"Would a user notice this change?"**

- If they'd see it in the UI, in their videos, or in their captured images — include it.
- If it affects reliability, performance, or data they'd experience — include it, but generalize it.
- If only a developer or operator would ever know — leave it out (roll it into one "reliability" line).

## Format

```markdown
# {version} — {date}

One-line summary of the release theme.

## NEW
- **Feature name** — What you can now do that you couldn't before.

## IMPROVED
- **What got better** — How the experience changed, not what code changed.

## FIXED
- **What was broken** — What you saw wrong, and that it's fixed now.
```

## Rules

- **Write to the user — use "you/your."** "Your source images are now cleaned up after a timelapse renders" — not "Fixed cleanup passing safe_name to a UUID filter."
- **Describe outcomes, not implementations.** What the user sees, never the code.
- **No jargon.** No CRUD, HMAC, mypy, OCI, migration numbers, ticket IDs, table names.
- **One sentence per item** (two max for a complex feature). No sub-bullets.
- **Categories:** NEW (couldn't before), IMPROVED (had it, now better), FIXED (was broken — say what the user saw wrong).
- **Version is CalVer** `YYYY.0M.MICRO` (zero-padded month). The filename is the version; the `#` heading is `{version} — {date}`.

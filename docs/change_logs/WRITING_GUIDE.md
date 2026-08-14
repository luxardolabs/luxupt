# Change Log Writing Guide (INTERNAL)

The **internal, technical** change log — for you, future-you, and anyone debugging "what changed in 2026.08.0." One markdown file per release, named by version (CalVer `YYYY.0M.MICRO`), stamped with its date. Plain docs — not served by the app.

For the customer-facing version, see `docs/release_notes/`.

## Audience

Engineers. Nothing here is filtered for end-user friendliness.

## Format

```markdown
# {version} — {date}

One-line release theme (optional).

## Issues Closed
- LUXUPT-NNN — Title — short note on what landed.

## Features
- What got built. Reference issue IDs.

## Refactors
- Architectural changes, renames, file moves.

## Schema / Migrations
- DB migrations included in this release. Note destructive vs additive.

## Infra / Ops
- Build, deploy, guard-pin, and env-var changes. Anything ops needs to know.

## Fixes
- Bug fixes with enough context to find them in git later.

## Notes
- Pre-/post-deploy steps, known issues, anything else worth remembering.
```

## Rules

- Reference **LUXUPT-NNN** issue IDs liberally — the back-link to LuxPM.
- Jargon and acronyms are fine. This is for engineers.
- No "user impact" framing — that belongs in `release_notes/`.
- Call out destructive operations (column drops, force pushes, data backfills) prominently.
- Pre-/post-deploy steps go at the **top** of their section, not buried.
- Historical SemVer releases (`1.1.x`) are re-versioned to CalVer by their release date; note the original tag in the theme line so git history stays findable.

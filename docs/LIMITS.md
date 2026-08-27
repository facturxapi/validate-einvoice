# Runtime limits

Measured on `saxonche==13.0.0` (SaxonC-HE 13.0), 27 August 2026. Figures
below are observations unless this Action sets them.

## What the Action does not cap

There is no Action-level file-count limit, file-size limit, or per-file
or process timeout. Inputs remain `files`, `syntax`, `fail-on`, and
`version`.

An 8 MiB text file was accepted locally (transform finished). That is
not a published maximum.

## Observed engine defaults (not configured here)

| Setting | Observed value |
|---|---|
| `jdk.xml.maxElementDepth` | 100 |
| `jdk.xml.entityExpansionLimit` | 2500 |

This change does not alter entity or URI resolution.

## Annotation text

Workflow-command escaping matches the GitHub toolkit
(`actions/toolkit` `packages/core/src/command.ts`):

- **A. Properties** (`file=`, `title=`): `%` → `%25`, then CR/LF →
  `%0D`/`%0A`, then `:` → `%3A`, then `,` → `%2C`.
- **B. User text** (after the second `::`): `%` → `%25`, then CR/LF
  only. Colons and commas stay readable (rule IDs).

The SVRL message body is then truncated to 220 characters.

## CI job timeouts

`timeout-minutes` is 20 on Self-test jobs and 15 on Supply-chain and
upstream-drift jobs. Those bound the runner job, not invoice size.

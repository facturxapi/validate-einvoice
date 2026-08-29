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

## DTD / protocol confine (C + B)

C (mandatory): stdlib `xml.parsers.expat` `StartDoctypeDeclHandler` refuses
any DOCTYPE before Saxon. The invoice file is not rewritten; Saxon
`source_file=` uses the same path and the same bytes that passed the
gate. `files[].sha256` is the SHA256 of those raw bytes. User invoices
are not newline-normalized. `ET.iterparse` is syntax detection only; it
is not this gate.

B (additional): SaxonC 13
`set_configuration_property('http://saxon.sf.net/feature/allowedProtocols',
'file')` blocks HTTP/HTTPS as an extra control. It does not replace C
(`file:` SYSTEM still needs C). Empty `allowedProtocols` is not used:
it breaks `stylesheet_file=` and `source_file=`.

## Annotation text

Workflow-command escaping matches the GitHub toolkit
(`actions/toolkit` `packages/core/src/command.ts`):

- **A. Properties** (`file=`, `title=`): `%` → `%25`, then CR/LF →
  `%0D`/`%0A`, then `:` → `%3A`, then `,` → `%2C`.
- **B. User text** (after the second `::`): `%` → `%25`, then CR/LF
  only. Colons and commas stay readable (rule IDs).

The SVRL message body is then truncated to 220 characters.

## Cross-OS bytes

`testdata/**/*.xml` is pinned `eol=lf` so fixture checkouts are LF on
Linux, macOS, and Windows. That pin is testdata only. User invoices are
hashed as received (raw file bytes). Validation reports are UTF-8 with
LF endings; they are written as bytes so Windows cannot inject CR LF.

## CI job timeouts

`timeout-minutes` is 20 on Self-test jobs, 15 on `selftest-gate`, and 15
on Supply-chain and upstream-drift jobs. Those bound the runner job, not
invoice size.

## CI token (GITHUB_TOKEN)

The Self-test workflow grants `contents: read` only. Same-run
`upload-artifact` / `download-artifact` do not take a token with
`actions: write` / `actions: read`. Jobs that `uses: ./` must not
hold Actions mutation rights.

# Validate EN16931 e-invoice

GitHub Action (and local CLI) that runs the **official ConnectingEurope
EN16931 1.3.16 XSLT** on CII / UBL XML invoices and reports every
`svrl:failed-assert`.

The Action executes the vendored stylesheets with SaxonC-HE 13.0
(`saxonche==13.0.0`) on `ubuntu-latest`. It does **not** call a remote
API, does **not** extract PDF attachments, and does **not** certify a
document. A green job means: the official 1.3.16 XSLT produced zero
`svrl:failed-assert` on the files you passed.

## 30-second usage

```yaml
- uses: ./
  with:
    files: invoices/**/*.xml
```

After this repository is published and tagged, pin a release instead of
`./`:

```yaml
- uses: OWNER/en16931-validate-action@v1
  with:
    files: invoices/**/*.xml
```

Copy-paste workflow: [`examples/validate-invoices.yml`](examples/validate-invoices.yml).

## Local self-test (no GitHub)

```bash
./scripts/selftest.sh
```

This installs `saxonche==13.0.0` in `.venv`, checks the 10 official
examples (must be green), checks the 10 mutants (must be red with the
ids in `testdata/MUTANTS.md`), compares two consecutive report hashes,
runs the unit tests, and runs the identity gate.

Same engine, one file at a time:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/validate.py --files 'testdata/official/*.xml'
```

## Inputs

| Name | Required | Default | Meaning |
|---|---|---|---|
| `files` | yes | — | Glob(s) of XML invoices, relative to the workspace. Separate several globs with a newline or a comma. Zero matches fail the job (exit 2). |
| `syntax` | no | `auto` | `auto` reads the document-element namespace. `cii` / `ubl` force one syntax and fail if the document does not match. |
| `fail-on` | no | `failed-assert` | `failed-assert`: any `svrl:failed-assert` fails the job. `never`: report only. Comma-separated ids (`BR-CO-15,BR-02`): fail only if one of those ids fires. |
| `version` | no | `1.3.16` | Only the vendored 1.3.16 artefacts are shipped. Any other value is a configuration error. |

## Outputs

| Name | Meaning |
|---|---|
| `verdict` | `pass` or `fail` |
| `failed-count` | Number of files with at least one `svrl:failed-assert` |
| `report` | Canonical JSON report (stable keys, no timestamps) |
| `report-sha256` | SHA256 of that JSON |
| `report-path` | Workspace-relative path of the written file (`en16931-report.json`) |

Each file in the JSON has: `path`, `syntax`, `verdict`,
`failed_assert_count`, `failed_assert_ids`, `sha256`, `xslt`,
`xslt_sha256`.

The job also writes:

- GitHub annotations (`::error file=…,title=<id>::`)
- a markdown table on the job summary

Invoice bytes are never logged.

## Architecture

`runs.using: composite` on `ubuntu-latest`:

1. `actions/setup-python@v5` (Python 3.12)
2. `pip install -r requirements.txt` (`saxonche==13.0.0`)
3. `src/validate.py --github`

Docker is not used: there is no image to publish, and the XSLT plus the
pinned engine already make the verdict reproducible. The CII / UBL
stylesheets are checked against their SHA256 on every run.

| Syntax | XSLT | SHA256 |
|---|---|---|
| CII | `vendor/en16931-1.3.16/xslt/EN16931-CII-validation.xslt` | `0b234dea2bbfee739b7761e607a992c17fab88773014ef56355b6158cfb1cc53` |
| UBL | `vendor/en16931-1.3.16/xslt/EN16931-UBL-validation.xslt` | `39f9d282867f1a49e7708d9e29a53da89643e1ee56f10cec1ebcf1277595fcbd` |

`auto` maps:

- `urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100` → CII
- `urn:oasis:names:specification:ubl:schema:xsd:Invoice-2` or `…CreditNote-2` → UBL

## What this does not claim

- Not a national CIUS (France, XRechnung profile, Peppol BIS, …).
  `XRechnung-O.xml` is an official EN16931 category-O example, not a
  CIUS proof.
- Not Factur-X / ZUGFeRD packaging, not PDF/A-3, not veraPDF.
- Not a product benchmark and not a certification.

## Licence

EUPL 1.2 for this Action. The vendored XSLT and the official examples
are ConnectingEurope EN16931 `validation-1.3.16`, also EUPL 1.2,
unmodified. Mutants are modified copies (see `NOTICE`).

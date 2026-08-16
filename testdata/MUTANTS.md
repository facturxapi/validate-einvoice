# Mutants — expected `svrl:failed-assert` ids (EN16931 1.3.16)

Ten modified copies of the official ConnectingEurope examples. Each file
has **one semantic change**. The originals in `official/` are untouched.

Engine of record: SaxonC-HE 13.0 (`saxonche==13.0.0`) with the vendored
XSLT 1.3.16. Ids are the SVRL `id` attributes, in document order.

| File | Class | Expected ids |
|---|---|---|
| `CII_example1.xml` | TOTAL (BT-112) | `BR-CO-15`, `BR-CO-16` |
| `CII_example3.xml` | VAT (BT-117) | `BR-S-09`, `BR-CO-14` |
| `CII_example5.xml` | MANDATORY (BT-1) | `BR-02` |
| `CII_business_example_01.xml` | LINE-SUM (BT-106) | `BR-CO-10`, `BR-CO-13` |
| `CII_business_example_02.xml` | TYPE (BT-3) | `BR-CL-01` |
| `CII_business_example_Z.xml` | ID-TRUNC (BT-31) | `BR-CO-09` |
| `CII-BR-CO-10-RoundingIssue.xml` | MANDATORY (BT-24) | `BR-01` |
| `XRechnung-O.xml` | DATE (BT-2) | `CII-DT-097` |
| `ubl-tc434-creditnote1.xml` | TOTAL UBL (BT-115) | `BR-CO-16` |
| `huf_example_cii.xml` | VAT-RATE (BT-119) | `BR-CO-17`, `BR-S-08`, `BR-S-09` |

Machine-readable copy: `expected-ids.json`.

A runner that reports **zero** failed-assert on any of these files, or
a different id list, has failed the mutant gate.

These XML files are **modified** (16 August 2026) relative to the
official examples. See `NOTICE` (EUPL article 5).

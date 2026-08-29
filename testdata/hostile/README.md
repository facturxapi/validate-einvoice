# Hostile DTD fixtures

Used only by `tests/test_dtd_gate.py` (temporary files with local canary
paths and a loopback HTTP server). Not globbed by selftest official or
mutant steps, nor by the Action jobs (`testdata/official/*.xml`,
`testdata/mutants/*.xml`).

Do not add DOCTYPE samples under `testdata/official/` or `testdata/mutants/`.

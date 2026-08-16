#!/usr/bin/env bash
# Local Definition of Done. Does not call GitHub.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${ROOT}/.venv/bin/python"
PIP="${ROOT}/.venv/bin/pip"
if [[ ! -x "$PYTHON" ]]; then
  python3 -m venv "${ROOT}/.venv"
  PYTHON="${ROOT}/.venv/bin/python"
  PIP="${ROOT}/.venv/bin/pip"
fi
"$PIP" install --disable-pip-version-check -q -r "${ROOT}/requirements.txt"

VALIDATE=("${PYTHON}" "${ROOT}/src/validate.py")
PROOF="${ROOT}/.builder/proofs"
mkdir -p "$PROOF"

fail() {
  echo "SELFTEST FAIL: $*" >&2
  exit 1
}

echo "=== 1. official examples must be GREEN (0 failed-assert, exit 0) ==="
set +e
"${VALIDATE[@]}" --files "testdata/official/*.xml" --report "${PROOF}/official-1.json"
OFFICIAL_RC=$?
set -e
[[ "$OFFICIAL_RC" -eq 0 ]] || fail "official run exit ${OFFICIAL_RC}, expected 0"
"$PYTHON" - <<'PY'
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent if False else Path(".")
report = json.loads(Path(".builder/proofs/official-1.json").read_text(encoding="utf-8"))
rows = report["files"]
if len(rows) != 10:
    print(f"expected 10 files, got {len(rows)}", file=sys.stderr)
    sys.exit(1)
bad = [r["path"] for r in rows if r["failed_assert_count"] != 0 or r["verdict"] != "pass"]
if bad:
    print("non-zero failed-assert: " + ", ".join(bad), file=sys.stderr)
    sys.exit(1)
if report["verdict"] != "pass" or report["failed_count"] != 0:
    print("official report is not a clean pass", file=sys.stderr)
    sys.exit(1)
print("official: 10 files, 0 failed-assert")
PY

echo "=== 2. mutants must be RED with exact ids (exit 1) ==="
set +e
"${VALIDATE[@]}" --files "testdata/mutants/*.xml" --report "${PROOF}/mutants.json"
MUTANT_RC=$?
set -e
[[ "$MUTANT_RC" -eq 1 ]] || fail "mutant run exit ${MUTANT_RC}, expected 1"
"$PYTHON" - <<'PY'
import json, sys
from pathlib import Path
expected = json.loads(Path("testdata/expected-ids.json").read_text(encoding="utf-8"))
report = json.loads(Path(".builder/proofs/mutants.json").read_text(encoding="utf-8"))
got = {}
for row in report["files"]:
    name = Path(row["path"]).name
    got[name] = row["failed_assert_ids"]
missing = sorted(set(expected) - set(got))
extra = sorted(set(got) - set(expected))
errors = []
if missing:
    errors.append("missing files: " + ", ".join(missing))
if extra:
    errors.append("unexpected files: " + ", ".join(extra))
for name, ids in expected.items():
    actual = got.get(name)
    if actual != ids:
        errors.append(f"{name}: expected {ids}, got {actual}")
    if not actual:
        errors.append(f"{name}: mutant produced zero failed-assert")
if report["verdict"] != "fail":
    errors.append("mutant report verdict is not fail")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
print("mutants: 10 files, exact ids, job failed")
PY

echo "=== 3. determinism: two consecutive official runs, identical hash ==="
set +e
"${VALIDATE[@]}" --files "testdata/official/*.xml" --report "${PROOF}/official-2.json"
OFFICIAL_RC2=$?
set -e
[[ "$OFFICIAL_RC2" -eq 0 ]] || fail "second official run exit ${OFFICIAL_RC2}, expected 0"
"$PYTHON" - <<'PY'
import hashlib, sys
from pathlib import Path

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

a = Path(".builder/proofs/official-1.json")
b = Path(".builder/proofs/official-2.json")
ha, hb = digest(a), digest(b)
print(f"run1 {ha}")
print(f"run2 {hb}")
if ha != hb:
    print("report hashes differ", file=sys.stderr)
    sys.exit(1)
Path(".builder/proofs/hashes.txt").write_text(f"{ha}\n{hb}\n", encoding="utf-8")
print("determinism: hashes identical")
PY

echo "=== 4. unit tests ==="
"$PYTHON" -m unittest discover -s tests -v

echo "=== 5. identity gate ==="
"$PYTHON" "${ROOT}/tests/identity_gate.py" | tee "${PROOF}/identity.txt"

echo
echo "SELFTEST PASS"

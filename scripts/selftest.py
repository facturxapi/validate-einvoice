#!/usr/bin/env python3
"""Portable Definition of Done. No GitHub. Same strict checks on every OS."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / ".builder" / "proofs"
REQUIREMENTS = ROOT / "requirements.txt"
VALIDATE = ROOT / "src" / "validate.py"
EXPECTED_IDS = ROOT / "testdata" / "expected-ids.json"


def venv_python() -> Path:
    override = os.environ.get("SELFTEST_PYTHON")
    if override:
        return Path(override)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return Path(sys.executable)
    win = os.name == "nt"
    scripts = "Scripts" if win else "bin"
    name = "python.exe" if win else "python"
    candidate = ROOT / ".venv" / scripts / name
    if not candidate.is_file():
        subprocess.check_call([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    return candidate


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(ROOT), text=True, check=check)


def fail(message: str) -> None:
    print(f"SELFTEST FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_oracle(python: Path, glob_pattern: str, report: Path) -> int:
    """Run the functional oracle.

    On Linux the production CLI (including the fail-closed preflight) is used.
    On other OS the same validate_files() path is imported — never a production
    environment bypass.
    """
    if sys.platform == "linux":
        completed = run(
            [str(python), str(VALIDATE), "--files", glob_pattern, "--report", str(report)],
            check=False,
        )
        return completed.returncode
    src = str(ROOT / "src")
    snippet = (
        "import json,sys; sys.path.insert(0,%r); import validate; "
        "from pathlib import Path; "
        "root=Path(%r); files=validate.expand_files([%r], cwd=root); "
        "result=validate.validate_files(files, syntax='auto', fail_on_raw='failed-assert', "
        "version='1.3.16', root=root, cwd=root); "
        "Path(%r).write_text(result['report_text'], encoding='utf-8'); "
        "sys.exit(result['exit_code'])"
    ) % (src, str(ROOT), glob_pattern, str(report))
    completed = run([str(python), "-c", snippet], check=False)
    return completed.returncode


def main() -> int:
    python = venv_python()
    PROOF.mkdir(parents=True, exist_ok=True)
    pip_cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)]
    run(pip_cmd)
    if sys.platform != "linux":
        print("NOTE: CLI preflight is Linux-only; functional oracle uses validate_files()")

    print("=== 1. official examples must be GREEN (0 failed-assert, exit 0) ===")
    official_1 = PROOF / "official-1.json"
    official_rc = run_oracle(python, "testdata/official/*.xml", official_1)
    if official_rc != 0:
        fail(f"official run exit {official_rc}, expected 0")
    report = load_json(official_1)
    rows = report["files"]
    if len(rows) != 10:
        fail(f"expected 10 files, got {len(rows)}")
    bad = [row["path"] for row in rows if row["failed_assert_count"] != 0 or row["verdict"] != "pass"]
    if bad:
        fail("non-zero failed-assert: " + ", ".join(bad))
    if report["verdict"] != "pass" or report["failed_count"] != 0:
        fail("official report is not a clean pass")
    print("official: 10 files, 0 failed-assert")

    print("=== 2. mutants must be RED with exact ids (exit 1) ===")
    mutants_path = PROOF / "mutants.json"
    mutants_rc = run_oracle(python, "testdata/mutants/*.xml", mutants_path)
    if mutants_rc != 1:
        fail(f"mutant run exit {mutants_rc}, expected 1")
    expected = load_json(EXPECTED_IDS)
    mutant_report = load_json(mutants_path)
    got = {Path(row["path"]).name: row["failed_assert_ids"] for row in mutant_report["files"]}
    errors: list[str] = []
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
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
    if mutant_report["verdict"] != "fail":
        errors.append("mutant report verdict is not fail")
    if errors:
        fail("\n".join(errors))
    print("mutants: 10 files, exact ids, job failed")

    print("=== 3. determinism: two consecutive official runs, identical hash ===")
    official_2 = PROOF / "official-2.json"
    official_again_rc = run_oracle(python, "testdata/official/*.xml", official_2)
    if official_again_rc != 0:
        fail(f"second official run exit {official_again_rc}, expected 0")
    hash1, hash2 = sha256_file(official_1), sha256_file(official_2)
    print(f"run1 {hash1}")
    print(f"run2 {hash2}")
    if hash1 != hash2:
        fail("report hashes differ")
    (PROOF / "hashes.txt").write_text(f"{hash1}\n{hash2}\n", encoding="utf-8")
    print("determinism: hashes identical")

    print("=== 4. unit tests (venv interpreter) ===")
    units = run(
        [str(python), "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", "test_*.py", "-v"],
        check=False,
    )
    if units.returncode != 0:
        fail(f"unit tests failed via {python} (exit {units.returncode})")
    print(f"unit tests: ran via {python.name} -m unittest discover")

    print("=== 5. identity gate ===")
    identity = run([str(python), str(ROOT / "tests" / "identity_gate.py")], check=False)
    if identity.returncode != 0:
        fail("identity gate failed")

    print()
    print("SELFTEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

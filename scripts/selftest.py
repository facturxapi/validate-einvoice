#!/usr/bin/env python3
"""Portable Definition of Done. No GitHub. Same strict checks on every OS."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / ".builder" / "proofs"
REQUIREMENTS = ROOT / "requirements.lock"
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


def main() -> int:
    python = venv_python()
    PROOF.mkdir(parents=True, exist_ok=True)
    pip_cmd = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--require-hashes",
        "-q",
        "-r",
        str(REQUIREMENTS),
    ]
    run(pip_cmd)

    print("=== 1. official examples must be GREEN (0 failed-assert, exit 0) ===")
    official_1 = PROOF / "official-1.json"
    official = run(
        [str(python), str(VALIDATE), "--files", "testdata/official/*.xml", "--report", str(official_1)],
        check=False,
    )
    if official.returncode != 0:
        fail(f"official run exit {official.returncode}, expected 0")
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
    mutants = run(
        [str(python), str(VALIDATE), "--files", "testdata/mutants/*.xml", "--report", str(mutants_path)],
        check=False,
    )
    if mutants.returncode != 1:
        fail(f"mutant run exit {mutants.returncode}, expected 1")
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
    official_again = run(
        [str(python), str(VALIDATE), "--files", "testdata/official/*.xml", "--report", str(official_2)],
        check=False,
    )
    if official_again.returncode != 0:
        fail(f"second official run exit {official_again.returncode}, expected 0")
    hash1, hash2 = sha256_file(official_1), sha256_file(official_2)
    print(f"run1 {hash1}")
    print(f"run2 {hash2}")
    if hash1 != hash2:
        fail("report hashes differ")
    (PROOF / "hashes.txt").write_text(f"{hash1}\n{hash2}\n", encoding="utf-8")
    print("determinism: hashes identical")

    print("=== 4. unit tests ===")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        fail(
            f"unit tests failed (failures={len(result.failures)} errors={len(result.errors)})"
        )
    print(f"unit tests: {result.testsRun} ran, 0 failures")

    print("=== 5. identity gate ===")
    identity = run([str(python), str(ROOT / "tests" / "identity_gate.py")], check=False)
    if identity.returncode != 0:
        fail("identity gate failed")

    print("=== 6. supply-chain gate ===")
    supply = run([str(python), str(ROOT / "scripts" / "check_supply_chain.py")], check=False)
    if supply.returncode != 0:
        fail("supply-chain gate failed\n" + (supply.stdout or "") + (supply.stderr or ""))

    print()
    print("SELFTEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

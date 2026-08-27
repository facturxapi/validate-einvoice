#!/usr/bin/env python3
"""Mutant tests for the supply-chain gate."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_supply_chain.py"
PYTHON = sys.executable


def run_gate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, str(root / "scripts" / "check_supply_chain.py")],
        cwd=str(root),
        text=True,
        capture_output=True,
    )


class SupplyChainGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CHECK.is_file():
            raise unittest.SkipTest("check_supply_chain.py missing")

    def test_gate_passes_on_clean_tree(self) -> None:
        proc = run_gate(ROOT)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_mutant_external_main_ref_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-main-ref-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            workflow = root / ".github" / "workflows" / "supply-chain.yml"
            text = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                text.replace(
                    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0",
                    "actions/checkout@main",
                ),
                encoding="utf-8",
            )
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)
            self.assertIn("external uses must be owner/repo@<40-char-sha>", proc.stderr)

    def test_mutant_unknown_external_sha_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-unknown-action-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            workflow = root / ".github" / "workflows" / "supply-chain.yml"
            text = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                text
                + "\n      - uses: unknown-owner/unknown-action@"
                + "a" * 40
                + " # probe\n",
                encoding="utf-8",
            )
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)
            self.assertIn("not listed in supply-chain/action-pins.yaml", proc.stderr)

    def test_mutant_missing_dependency_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-missing-hash-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            lock = root / "requirements.lock"
            lock.write_text("saxonche==13.0.0\n", encoding="utf-8")
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)
            self.assertTrue(
                "at least three wheel hashes" in proc.stderr
                or "must name one package with hashes" in proc.stderr,
                msg=proc.stderr,
            )

    def test_mutant_lock_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-lock-drift-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            lock = root / "requirements.lock"
            lock.write_text(
                lock.read_text(encoding="utf-8").replace(
                    "04388b0625617df696e26f6c04b06479111a55e6cd86a7e00fbf5e2a38446523",
                    "0" * 64,
                ),
                encoding="utf-8",
            )
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)
            self.assertIn("tree digest drift", proc.stderr)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Mutant tests for the supply-chain gate."""

from __future__ import annotations

import hashlib
import importlib.util
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


def load_gate_module(path: Path | None = None):
    target = path or CHECK
    spec = importlib.util.spec_from_file_location("ve_check_supply_chain", target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def refresh_digest_line(root: Path, rel: str) -> None:
    digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    tree = root / "supply-chain" / "tree.sha256"
    lines = []
    for line in tree.read_text(encoding="utf-8").splitlines():
        if line.endswith(f"  {rel}"):
            lines.append(f"{digest}  {rel}")
        else:
            lines.append(line)
    tree.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Exact top-level permissions block from d13ec9d5e9bbfd323103d1084de410ee309842a2.
D13EC9D5_SELFTEST_PERMISSIONS = """name: Self-test EN16931 action
on:
  push:
permissions:
  contents: read
  actions: write
jobs:
  selftest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
  action-official:
    runs-on: ubuntu-latest
    steps:
      - uses: ./
  action-mutants:
    runs-on: ubuntu-latest
    steps:
      - uses: ./
  selftest-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
"""


class WorkflowPermissionTests(unittest.TestCase):
    """Least-privilege contract for workflows that `uses: ./`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = load_gate_module()

    def test_selftest_top_level_permissions_exactly_contents_read(self) -> None:
        path = ROOT / ".github" / "workflows" / "selftest.yml"
        info = self.gate.parse_workflow_permissions(path.read_text(encoding="utf-8"))
        self.assertEqual(info["top_permissions"], {"contents": "read"})
        self.assertNotIn("actions", info["top_permissions"])
        self.assertFalse(self.gate.grants_actions_write(info["top_permissions"]))
        raw = path.read_text(encoding="utf-8")
        live = [
            self.gate.strip_yaml_comment(line).strip()
            for line in raw.splitlines()
            if self.gate.strip_yaml_comment(line).strip()
        ]
        self.assertNotIn("actions: write", live)

    def test_action_official_and_mutants_have_no_actions_write(self) -> None:
        path = ROOT / ".github" / "workflows" / "selftest.yml"
        info = self.gate.parse_workflow_permissions(path.read_text(encoding="utf-8"))
        for name in ("action-official", "action-mutants"):
            job = info["jobs"][name]
            self.assertTrue(job["uses_local"], msg=name)
            self.assertFalse(
                self.gate.grants_actions_write(job["permissions"]),
                msg=f"{name} permissions={job['permissions']!r}",
            )

    def test_no_local_uses_workflow_has_global_actions_write(self) -> None:
        for path in self.gate.iter_gha_yaml_files():
            info = self.gate.parse_workflow_permissions(path.read_text(encoding="utf-8"))
            if info["has_local_uses"]:
                self.assertFalse(
                    self.gate.grants_actions_write(info["top_permissions"]),
                    msg=path.as_posix(),
                )

    def test_prechange_d13ec9d5_global_actions_write_is_detected(self) -> None:
        info = self.gate.parse_workflow_permissions(D13EC9D5_SELFTEST_PERMISSIONS)
        self.assertEqual(
            info["top_permissions"],
            {"contents": "read", "actions": "write"},
        )
        self.assertTrue(self.gate.grants_actions_write(info["top_permissions"]))
        self.assertTrue(info["jobs"]["action-official"]["uses_local"])
        self.assertTrue(info["jobs"]["action-mutants"]["uses_local"])
        self.assertFalse(self.gate.grants_actions_write(info["jobs"]["action-official"]["permissions"]))
        self.assertFalse(self.gate.grants_actions_write(info["jobs"]["action-mutants"]["permissions"]))

    def test_commented_actions_write_is_ignored(self) -> None:
        text = """name: x
permissions:
  contents: read
  # actions: write
jobs:
  action-official:
    steps:
      - uses: ./
"""
        info = self.gate.parse_workflow_permissions(text)
        self.assertEqual(info["top_permissions"], {"contents": "read"})
        self.assertFalse(self.gate.grants_actions_write(info["top_permissions"]))
        self.assertTrue(info["has_local_uses"])

    def test_flow_mapping_actions_write_is_detected(self) -> None:
        text = """name: x
permissions: { contents: read, actions: write }
jobs:
  action-official:
    steps:
      - uses: ./
"""
        info = self.gate.parse_workflow_permissions(text)
        self.assertTrue(self.gate.grants_actions_write(info["top_permissions"]))

    def test_job_level_actions_write_on_local_uses_is_detected(self) -> None:
        text = """name: x
permissions:
  contents: read
jobs:
  action-official:
    permissions:
      actions: write
    steps:
      - uses: ./
  action-mutants:
    steps:
      - uses: ./
"""
        info = self.gate.parse_workflow_permissions(text)
        self.assertFalse(self.gate.grants_actions_write(info["top_permissions"]))
        self.assertTrue(self.gate.grants_actions_write(info["jobs"]["action-official"]["permissions"]))
        self.assertFalse(self.gate.grants_actions_write(info["jobs"]["action-mutants"]["permissions"]))

    def test_mutant_global_actions_write_on_local_uses_fails_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-actions-write-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            workflow = root / ".github" / "workflows" / "selftest.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "permissions:\n  contents: read\n",
                    "permissions:\n  contents: read\n  actions: write\n",
                ),
                encoding="utf-8",
            )
            refresh_digest_line(root, ".github/workflows/selftest.yml")
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertIn("uses: ./", proc.stderr)
            self.assertIn("permissions.actions=write", proc.stderr)
            self.assertIn(".github/workflows/selftest.yml", proc.stderr)

    def test_mutant_job_actions_write_on_local_uses_fails_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-job-actions-write-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            workflow = root / ".github" / "workflows" / "selftest.yml"
            text = workflow.read_text(encoding="utf-8")
            needle = "  action-official:\n    name:"
            repl = (
                "  action-official:\n"
                "    permissions:\n"
                "      actions: write\n"
                "    name:"
            )
            self.assertIn(needle, text)
            workflow.write_text(text.replace(needle, repl, 1), encoding="utf-8")
            refresh_digest_line(root, ".github/workflows/selftest.yml")
            proc = run_gate(root)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertIn("action-official", proc.stderr)
            self.assertIn("permissions.actions=write", proc.stderr)

    def test_mutant_comment_only_actions_write_still_passes_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-comment-actions-") as td:
            root = Path(td) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", ".builder"))
            workflow = root / ".github" / "workflows" / "selftest.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "permissions:\n  contents: read\n",
                    "permissions:\n  contents: read\n  # actions: write\n",
                ),
                encoding="utf-8",
            )
            refresh_digest_line(root, ".github/workflows/selftest.yml")
            proc = run_gate(root)
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

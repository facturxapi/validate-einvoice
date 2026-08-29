#!/usr/bin/env python3
"""Cross-OS canonical report bytes. Fixtures LF; user invoices not normalized."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import validate  # noqa: E402

LINUX_OFFICIAL_REPORT = "eaa0dc88dc46f88edca7808556f27faf2843d7e454f272a944a8e289a04cd717"
LINUX_MUTANT_REPORT = "5ee08caf8a868a5d1a2333cb3d912507bb312a619e5f7daae02eaa6d94f9d85b"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lf_to_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def testdata_xml() -> list[Path]:
    return sorted((ROOT / "testdata").rglob("*.xml"))


def file_row(kind: str, name: str, file_sha: str, ids: list[str]) -> dict:
    syntax = "UBL" if name.startswith("ubl-") else "CII"
    xslt = validate.UBL_XSLT_REL if syntax == "UBL" else validate.CII_XSLT_REL
    xslt_sha = validate.UBL_XSLT_SHA256 if syntax == "UBL" else validate.CII_XSLT_SHA256
    return {
        "failed_assert_count": len(ids),
        "failed_assert_ids": list(ids),
        "path": f"testdata/{kind}/{name}",
        "sha256": file_sha,
        "syntax": syntax,
        "verdict": "fail" if ids else "pass",
        "xslt": xslt,
        "xslt_sha256": xslt_sha,
    }


def reconstruct_report(kind: str, expected: dict[str, list[str]], *, crlf: bool) -> tuple[str, str]:
    names = sorted(path.name for path in (ROOT / "testdata" / "official").glob("*.xml"))
    rows = []
    for name in names:
        raw = (ROOT / "testdata" / kind / name).read_bytes()
        on_disk = lf_to_crlf(raw) if crlf else raw
        rows.append(file_row(kind, name, sha256_bytes(on_disk), expected[name]))
    rows.sort(key=lambda item: item["path"])
    payload = {
        "engine": validate.ENGINE_NAME,
        "engine_pkg": validate.ENGINE_PKG_PIN,
        "fail_on": "failed-assert",
        "failed_count": sum(1 for row in rows if row["failed_assert_count"] > 0),
        "files": rows,
        "syntax": "auto",
        "verdict": "fail" if any(row["verdict"] == "fail" for row in rows) else "pass",
        "version": validate.SUPPORTED_VERSION,
    }
    text = validate.canonical_json(payload)
    return text, validate.sha256_text(text)


class TestdataLfTests(unittest.TestCase):
    def test_testdata_xml_contains_no_cr(self) -> None:
        files = testdata_xml()
        self.assertGreaterEqual(len(files), 20)
        for path in files:
            data = path.read_bytes()
            self.assertNotIn(b"\r", data, msg=path.as_posix())
            self.assertIn(b"\n", data, msg=path.as_posix())

    def test_gitattributes_pins_testdata_xml_only(self) -> None:
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("testdata/**/*.xml text eol=lf", text)
        assignments = []
        for line in text.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped:
                assignments.append(stripped)
        self.assertNotIn("*.xml text eol=lf", assignments)
        self.assertNotIn("*.xml eol=lf", assignments)


class FileHashDocumentsOldBugTests(unittest.TestCase):
    def test_lf_vs_crlf_changes_files_sha256(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example1.xml"
        raw = path.read_bytes()
        self.assertNotIn(b"\r", raw)
        crlf = lf_to_crlf(raw)
        self.assertIn(b"\r\n", crlf)
        self.assertNotEqual(sha256_bytes(raw), sha256_bytes(crlf))
        with tempfile.TemporaryDirectory() as tmp:
            lf_path = Path(tmp) / "lf.xml"
            crlf_path = Path(tmp) / "crlf.xml"
            lf_path.write_bytes(raw)
            crlf_path.write_bytes(crlf)
            self.assertEqual(validate.sha256_file(lf_path), sha256_bytes(raw))
            self.assertEqual(validate.sha256_file(crlf_path), sha256_bytes(crlf))
            self.assertNotEqual(validate.sha256_file(lf_path), validate.sha256_file(crlf_path))


class CanonicalReportWriteTests(unittest.TestCase):
    def test_canonical_json_is_lf_only(self) -> None:
        text = validate.canonical_json({"k": 1, "files": [{"path": "a.xml"}]})
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)
        self.assertEqual(text.encode("utf-8").count(b"\n"), 1)

    def test_write_canonical_report_does_not_inject_crlf(self) -> None:
        text = validate.canonical_json({"verdict": "pass", "files": []})
        self.assertNotIn("\r", text)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nested" / "report.json"
            validate.write_canonical_report(dest, text)
            raw = dest.read_bytes()
        self.assertEqual(raw, text.encode("utf-8"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))

    def test_write_canonical_report_rejects_cr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bad.json"
            with self.assertRaises(ValueError):
                validate.write_canonical_report(dest, '{"k":1}\r\n')

    def test_main_uses_byte_write_not_write_text(self) -> None:
        source = inspect.getsource(validate.main)
        self.assertIn("write_canonical_report", source)
        self.assertNotIn("write_text", source)
        helper = inspect.getsource(validate.write_canonical_report)
        self.assertIn("write_bytes", helper)
        self.assertNotIn("write_text", helper)

    def test_selftest_does_not_write_text_reports(self) -> None:
        source = (ROOT / "scripts" / "selftest.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text", source)
        self.assertIn("write_bytes", source)


class UserInvoiceNotNormalizedTests(unittest.TestCase):
    def test_sha256_file_is_raw_open_rb(self) -> None:
        source = inspect.getsource(validate.sha256_file)
        self.assertIn('"rb"', source)
        self.assertIn("open", source)
        self.assertNotIn("replace", source)
        self.assertNotIn("newline", source)
        self.assertNotIn("splitlines", source)

    def test_sha256_file_does_not_strip_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invoice.xml"
            crlf = b"<a>\r\n</a>\n"
            lf = b"<a>\n</a>\n"
            path.write_bytes(crlf)
            self.assertEqual(validate.sha256_file(path), sha256_bytes(crlf))
            path.write_bytes(lf)
            self.assertEqual(validate.sha256_file(path), sha256_bytes(lf))
            self.assertNotEqual(sha256_bytes(crlf), sha256_bytes(lf))

    def test_validate_files_hashes_via_sha256_file(self) -> None:
        source = inspect.getsource(validate.validate_files)
        self.assertIn("data = refuse_invoice_dtd(path)", source)
        self.assertIn("hashlib.sha256(data).hexdigest()", source)
        self.assertNotIn("sha256_file(path)", source)
        self.assertNotIn("replace(b", source)
        self.assertNotIn("tostring", source)
        self.assertNotIn("write_bytes", source)
        self.assertNotIn("write_text", source)
        refuse_src = inspect.getsource(validate.refuse_invoice_dtd)
        self.assertIn("path.read_bytes()", refuse_src)
        self.assertNotIn("tostring", refuse_src)
        self.assertNotIn("serialize", refuse_src)
        self.assertNotIn("write_bytes", refuse_src)
        self.assertNotIn("write_text", refuse_src)
        module = Path(validate.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".replace(b\"\\r\\n\"", module)
        self.assertNotIn(".replace('\\r\\n'", module)


class LinuxReconstructionTests(unittest.TestCase):
    def test_official_and_mutant_reports_match_known_lf_hashes(self) -> None:
        expected_mut = json.loads((ROOT / "testdata" / "expected-ids.json").read_text(encoding="utf-8"))
        names = sorted(path.name for path in (ROOT / "testdata" / "official").glob("*.xml"))
        self.assertEqual(len(names), 10)
        expected_off = {name: [] for name in names}
        text_off, hash_off = reconstruct_report("official", expected_off, crlf=False)
        text_mut, hash_mut = reconstruct_report("mutants", expected_mut, crlf=False)
        self.assertNotIn("\r", text_off)
        self.assertNotIn("\r", text_mut)
        self.assertEqual(hash_off, LINUX_OFFICIAL_REPORT)
        self.assertEqual(hash_mut, LINUX_MUTANT_REPORT)
        _, hash_off_crlf = reconstruct_report("official", expected_off, crlf=True)
        _, hash_mut_crlf = reconstruct_report("mutants", expected_mut, crlf=True)
        self.assertNotEqual(hash_off, hash_off_crlf)
        self.assertNotEqual(hash_mut, hash_mut_crlf)


class SelftestGateScriptTests(unittest.TestCase):
    def test_gate_script_passes_on_identical_os_cells(self) -> None:
        script = ROOT / "scripts" / "check_selftest_gate.py"
        self.assertTrue(script.is_file())
        names = sorted(path.name for path in (ROOT / "testdata" / "official").glob("*.xml"))
        official = validate.canonical_json(
            {
                "engine": validate.ENGINE_NAME,
                "engine_pkg": validate.ENGINE_PKG_PIN,
                "fail_on": "failed-assert",
                "failed_count": 0,
                "files": [file_row("official", name, "a" * 64, []) for name in names],
                "syntax": "auto",
                "verdict": "pass",
                "version": validate.SUPPORTED_VERSION,
            }
        )
        expected = json.loads((ROOT / "testdata" / "expected-ids.json").read_text(encoding="utf-8"))
        mutant_files = [file_row("mutants", name, "b" * 64, expected[name]) for name in sorted(expected)]
        mutants = validate.canonical_json(
            {
                "engine": validate.ENGINE_NAME,
                "engine_pkg": validate.ENGINE_PKG_PIN,
                "fail_on": "failed-assert",
                "failed_count": 10,
                "files": mutant_files,
                "syntax": "auto",
                "verdict": "fail",
                "version": validate.SUPPORTED_VERSION,
            }
        )
        digest_lines = []
        for row in json.loads(official)["files"]:
            digest_lines.append(f"{row['sha256']}  {row['path']}")
        for row in json.loads(mutants)["files"]:
            digest_lines.append(f"{row['sha256']}  {row['path']}")
        digest = "\n".join(digest_lines) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
                cell = root / f"selftest-reports-{os_name}-py3.13"
                cell.mkdir()
                (cell / "official.json").write_bytes(official.encode("utf-8"))
                (cell / "mutants.json").write_bytes(mutants.encode("utf-8"))
                (cell / "testdata.sha256").write_bytes(digest.encode("utf-8"))
            proc = subprocess.run(
                [sys.executable, str(script), str(root)],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("SELFTEST-GATE PASS", proc.stdout)

    def test_gate_script_fails_on_windows_byte_mismatch(self) -> None:
        script = ROOT / "scripts" / "check_selftest_gate.py"
        names = sorted(path.name for path in (ROOT / "testdata" / "official").glob("*.xml"))
        official = validate.canonical_json(
            {
                "engine": validate.ENGINE_NAME,
                "engine_pkg": validate.ENGINE_PKG_PIN,
                "fail_on": "failed-assert",
                "failed_count": 0,
                "files": [file_row("official", name, "a" * 64, []) for name in names],
                "syntax": "auto",
                "verdict": "pass",
                "version": validate.SUPPORTED_VERSION,
            }
        )
        expected = json.loads((ROOT / "testdata" / "expected-ids.json").read_text(encoding="utf-8"))
        mutants = validate.canonical_json(
            {
                "engine": validate.ENGINE_NAME,
                "engine_pkg": validate.ENGINE_PKG_PIN,
                "fail_on": "failed-assert",
                "failed_count": 10,
                "files": [file_row("mutants", name, "b" * 64, expected[name]) for name in sorted(expected)],
                "syntax": "auto",
                "verdict": "fail",
                "version": validate.SUPPORTED_VERSION,
            }
        )
        digest_lines = [f"{row['sha256']}  {row['path']}" for row in json.loads(official)["files"]]
        digest_lines.extend(f"{row['sha256']}  {row['path']}" for row in json.loads(mutants)["files"])
        digest = "\n".join(digest_lines) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
                cell = root / f"selftest-reports-{os_name}-py3.13"
                cell.mkdir()
                payload = official.encode("utf-8")
                if os_name == "windows-latest":
                    payload = official.replace("\n", "\r\n").encode("utf-8")
                (cell / "official.json").write_bytes(payload)
                (cell / "mutants.json").write_bytes(mutants.encode("utf-8"))
                (cell / "testdata.sha256").write_bytes(digest.encode("utf-8"))
            proc = subprocess.run(
                [sys.executable, str(script), str(root)],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("SELFTEST-GATE FAIL", proc.stderr)
        self.assertTrue("contains CR" in proc.stderr or "bytes !=" in proc.stderr, msg=proc.stderr)


if __name__ == "__main__":
    unittest.main()

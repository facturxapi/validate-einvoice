#!/usr/bin/env python3
"""Unit tests for the EN16931 validator helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import validate  # noqa: E402


class ExpandFilesTests(unittest.TestCase):
    def test_glob_official_yields_ten(self) -> None:
        files = validate.expand_files(["testdata/official/*.xml"], cwd=ROOT)
        names = sorted(path.name for path in files)
        self.assertEqual(len(names), 10)
        self.assertIn("CII_example1.xml", names)
        self.assertIn("ubl-tc434-creditnote1.xml", names)

    def test_no_match_is_config_error(self) -> None:
        with self.assertRaises(validate.ConfigError):
            validate.expand_files(["testdata/official/*.pdf"], cwd=ROOT)


class SyntaxTests(unittest.TestCase):
    def test_detect_cii(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example1.xml"
        self.assertEqual(validate.detect_syntax(path), "CII")

    def test_detect_ubl(self) -> None:
        path = ROOT / "testdata" / "official" / "ubl-tc434-creditnote1.xml"
        self.assertEqual(validate.detect_syntax(path), "UBL")

    def test_forced_mismatch(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example1.xml"
        with self.assertRaises(validate.ConfigError):
            validate.resolve_syntax(path, "ubl")


class FailOnTests(unittest.TestCase):
    def test_default_aliases(self) -> None:
        self.assertEqual(validate.parse_fail_on("failed-assert")["mode"], "failed-assert")
        self.assertEqual(validate.parse_fail_on("never")["mode"], "never")
        parsed = validate.parse_fail_on("BR-CO-15,BR-02")
        self.assertEqual(parsed["mode"], "ids")
        self.assertEqual(parsed["ids"], ["BR-CO-15", "BR-02"])

    def test_file_triggers(self) -> None:
        row = {"failed_assert_ids": ["BR-CO-15", "BR-CO-16"]}
        self.assertTrue(
            validate.file_triggers_fail(row, {"mode": "failed-assert", "ids": []})
        )
        self.assertFalse(validate.file_triggers_fail(row, {"mode": "never", "ids": []}))
        self.assertTrue(
            validate.file_triggers_fail(row, {"mode": "ids", "ids": ["BR-02", "BR-CO-16"]})
        )
        self.assertFalse(
            validate.file_triggers_fail(row, {"mode": "ids", "ids": ["BR-01"]})
        )


class SvrlParseTests(unittest.TestCase):
    def test_ids_in_document_order(self) -> None:
        svrl = """
        <svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">
          <svrl:failed-assert id="BR-S-09" location="/*[1]" test="true()">
            <svrl:text>one</svrl:text>
          </svrl:failed-assert>
          <svrl:failed-assert id="BR-CO-14" location="/*[1]" test="true()">
            <svrl:text>two</svrl:text>
          </svrl:failed-assert>
        </svrl:schematron-output>
        """
        rows = validate.parse_failed_asserts(svrl)
        self.assertEqual([row["id"] for row in rows], ["BR-S-09", "BR-CO-14"])
        self.assertEqual(rows[0]["text"], "one")


class ReportHashTests(unittest.TestCase):
    def test_canonical_json_is_stable(self) -> None:
        payload = {
            "engine": "SaxonC-HE 13.0",
            "files": [{"path": "a.xml", "verdict": "pass"}],
            "version": "1.3.16",
        }
        first = validate.canonical_json(payload)
        second = validate.canonical_json(payload)
        self.assertEqual(first, second)
        self.assertEqual(validate.sha256_text(first), validate.sha256_text(second))
        self.assertTrue(first.endswith("\n"))

    def test_display_path_stays_relative(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example1.xml"
        shown = validate.display_path(path, cwd=ROOT)
        self.assertEqual(shown, "testdata/official/CII_example1.xml")
        self.assertFalse(shown.startswith("/"))


class XsltPinTests(unittest.TestCase):
    def test_vendored_sha256(self) -> None:
        mapping = validate.load_xslt(ROOT, "1.3.16")
        self.assertEqual(mapping["CII"]["sha256"], validate.CII_XSLT_SHA256)
        self.assertEqual(mapping["UBL"]["sha256"], validate.UBL_XSLT_SHA256)

    def test_unknown_version(self) -> None:
        with self.assertRaises(validate.ConfigError):
            validate.load_xslt(ROOT, "9.9.9")


class WriteReportTests(unittest.TestCase):
    def test_report_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "report.json"
            dest.write_text(validate.canonical_json({"k": 1}), encoding="utf-8")
            self.assertEqual(dest.read_text(encoding="utf-8"), '{"k":1}\n')


class GithubOutputWindowsTests(unittest.TestCase):
    def test_github_output_is_lf_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "github_output"
            dest.write_bytes(b"")
            previous = os.environ.get("GITHUB_OUTPUT")
            os.environ["GITHUB_OUTPUT"] = str(dest)
            try:
                validate.write_github_output(
                    {"verdict": "fail", "failed-count": "1"},
                    '{"verdict":"fail"}\n',
                )
            finally:
                if previous is None:
                    os.environ.pop("GITHUB_OUTPUT", None)
                else:
                    os.environ["GITHUB_OUTPUT"] = previous
            raw = dest.read_bytes()
            self.assertIn(b"verdict=fail\n", raw)
            self.assertNotIn(b"verdict=fail\r\n", raw)
            self.assertNotIn(b"\r", raw)

    def test_emit_line_survives_cp1252_sigma(self) -> None:
        class FakeCp1252:
            encoding = "cp1252"
            buffer = None

            def __init__(self) -> None:
                self.chunks: list[str] = []

            def write(self, text: str) -> int:
                text.encode("cp1252")
                self.chunks.append(text)
                return len(text)

            def flush(self) -> None:
                return None

        sink = FakeCp1252()
        previous = sys.stdout
        sys.stdout = sink  # type: ignore[assignment]
        try:
            with self.assertRaises(UnicodeEncodeError):
                print("sum \u03a3 of invoice lines")
            validate.emit_line("sum \u03a3 of invoice lines")
        finally:
            sys.stdout = previous
        self.assertTrue(any("sum" in chunk for chunk in sink.chunks))


if __name__ == "__main__":
    unittest.main()

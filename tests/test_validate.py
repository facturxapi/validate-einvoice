#!/usr/bin/env python3
"""Unit tests for the EN16931 validator helpers."""

from __future__ import annotations

import json
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
        for version, pins in validate.VENDORED_XSLT.items():
            mapping = validate.load_xslt(ROOT, version)
            for syntax, (logical, sha) in pins.items():
                self.assertEqual(mapping[syntax]["sha256"], sha, version)
                self.assertEqual(mapping[syntax]["logical"], logical)

    def test_default_is_latest_vendored(self) -> None:
        self.assertEqual(validate.DEFAULT_VERSION, "1.3.17")
        self.assertIn("1.3.16", validate.VENDORED_XSLT)

    def test_upstream_pin_matches_default(self) -> None:
        pins = json.loads((ROOT / "vendor" / "upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(pins["pinned_version"], validate.DEFAULT_VERSION)
        for syntax, key in (("CII", "cii"), ("UBL", "ubl")):
            self.assertEqual(
                pins["assets"][key]["xslt_sha256"],
                validate.VENDORED_XSLT[validate.DEFAULT_VERSION][syntax][1],
            )

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


class FailOnIdCheckTests(unittest.TestCase):
    def ids(self, version: str) -> set[str]:
        return validate.xslt_rule_ids(validate.load_xslt(ROOT, version))

    def test_renamed_id_is_refused_with_new_name(self) -> None:
        fail_on = validate.parse_fail_on("BR-AF-05,BR-02")
        with self.assertRaisesRegex(validate.ConfigError, r"BR-AF-05 \(renamed BR-IP-05 in 1\.3\.17\)"):
            validate.check_fail_on_ids(fail_on, self.ids("1.3.17"), "1.3.17")

    def test_removed_id_is_refused(self) -> None:
        fail_on = validate.parse_fail_on("CII-SR-471")
        with self.assertRaisesRegex(validate.ConfigError, r"CII-SR-471 \(removed in 1\.3\.17\)"):
            validate.check_fail_on_ids(fail_on, self.ids("1.3.17"), "1.3.17")

    def test_typo_is_refused(self) -> None:
        fail_on = validate.parse_fail_on("BR-CO-1O")
        with self.assertRaisesRegex(validate.ConfigError, "BR-CO-1O"):
            validate.check_fail_on_ids(fail_on, self.ids("1.3.17"), "1.3.17")

    def test_known_ids_pass_per_version(self) -> None:
        validate.check_fail_on_ids(validate.parse_fail_on("BR-IG-05,BR-IP-10"), self.ids("1.3.17"), "1.3.17")
        validate.check_fail_on_ids(validate.parse_fail_on("BR-AF-05,CII-SR-471"), self.ids("1.3.16"), "1.3.16")

    def test_modes_without_ids_are_not_checked(self) -> None:
        for raw in ("failed-assert", "never"):
            validate.check_fail_on_ids(validate.parse_fail_on(raw), set(), "1.3.17")

    def test_rename_table_matches_vendored_xslt(self) -> None:
        old, new = self.ids("1.3.16"), self.ids("1.3.17")
        for before, after in validate.RENAMED_IN_1_3_17.items():
            self.assertIn(before, old)
            self.assertNotIn(before, new)
            self.assertIn(after, new)
        for removed in validate.REMOVED_IN_1_3_17:
            self.assertIn(removed, old)
            self.assertNotIn(removed, new)


if __name__ == "__main__":
    unittest.main()

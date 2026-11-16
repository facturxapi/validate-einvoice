#!/usr/bin/env python3
"""Unit tests for the EN16931 validator helpers."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import unittest.mock
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

    def test_default_is_the_release_in_force(self) -> None:
        # 1.3.17 is effective from 15/11/26 (Commission registry).
        self.assertEqual(validate.DEFAULT_VERSION, "1.3.17")
        self.assertIn("1.3.16", validate.VENDORED_XSLT)

    def test_upstream_pin_is_newest_vendored(self) -> None:
        pins = json.loads((ROOT / "vendor" / "upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(pins["pinned_version"], "1.3.17")
        for syntax, key in (("CII", "cii"), ("UBL", "ubl")):
            self.assertEqual(
                pins["assets"][key]["xslt_sha256"],
                validate.VENDORED_XSLT[pins["pinned_version"]][syntax][1],
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


RULE_TEXT_RE = re.compile(
    r'<xsl:attribute name="id">([^<]+)</xsl:attribute>.*?<svrl:text>(.*?)</svrl:text>',
    re.DOTALL,
)


def rule_texts(version: str, syntax: str) -> dict[str, str]:
    text = (ROOT / validate.VENDORED_XSLT[version][syntax][0]).read_text(encoding="utf-8")
    rules: dict[str, str] = {}
    for rule, message in RULE_TEXT_RE.findall(text):
        message = re.sub(r"\s+", " ", message).strip()
        rules.setdefault(rule, message.replace(f"[{rule}]", "", 1))
    return rules


class FailOnIdResolveTests(unittest.TestCase):
    def ids(self, version: str) -> dict[str, set[str]]:
        return validate.xslt_rule_ids(validate.load_xslt(ROOT, version))

    def resolve(self, raw: str, version: str) -> tuple[dict, list[str]]:
        return validate.resolve_fail_on_ids(validate.parse_fail_on(raw), self.ids(version), version)

    def test_renamed_id_warns_and_filters_on_new_name_per_syntax(self) -> None:
        fail_on, warnings = self.resolve("BR-AF-05,BR-02", "1.3.17")
        self.assertEqual(fail_on["watch"]["CII"], ["BR-IP-05", "BR-02"])
        self.assertEqual(fail_on["watch"]["UBL"], ["BR-IG-05", "BR-02"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("BR-AF-05 was renamed in EN16931 1.3.17", warnings[0])
        self.assertIn("BR-IP-05 in CII and BR-IG-05 in UBL", warnings[0])

    def test_new_id_on_previous_version_warns_and_filters_on_old_name(self) -> None:
        fail_on, warnings = self.resolve("BR-IG-05", "1.3.16")
        self.assertEqual(fail_on["watch"], {"CII": ["BR-AG-05"], "UBL": ["BR-AF-05"]})
        self.assertEqual(len(warnings), 1)
        self.assertIn("BR-IG-05 is a 1.3.17 id; with version 1.3.16", warnings[0])

    def test_removed_id_warns_and_is_ignored(self) -> None:
        fail_on, warnings = self.resolve("CII-SR-471,BR-02", "1.3.17")
        self.assertEqual(fail_on["watch"], {"CII": ["BR-02"], "UBL": ["BR-02"]})
        self.assertEqual(len(warnings), 1)
        self.assertIn("CII-SR-471 was removed in EN16931 1.3.17", warnings[0])

    def test_unknown_id_is_refused(self) -> None:
        for version in validate.VENDORED_XSLT:
            with self.assertRaisesRegex(validate.ConfigError, r"could never fire: BR-CO-1O$"):
                self.resolve("BR-02,BR-CO-1O", version)

    def test_known_ids_resolve_silently(self) -> None:
        fail_on, warnings = self.resolve("BR-IG-05,BR-IP-10,CII-SR-100", "1.3.17")
        self.assertEqual(warnings, [])
        self.assertEqual(fail_on["watch"]["CII"], ["BR-IG-05", "BR-IP-10", "CII-SR-100"])
        self.assertEqual(fail_on["watch"]["UBL"], ["BR-IG-05", "BR-IP-10"])
        fail_on, warnings = self.resolve("BR-AF-05,CII-SR-471", "1.3.16")
        self.assertEqual(warnings, [])
        self.assertEqual(fail_on["watch"]["CII"], ["BR-AF-05", "CII-SR-471"])

    def test_duplicates_after_translation_are_watched_once(self) -> None:
        fail_on, _ = self.resolve("BR-AF-05,BR-IP-05", "1.3.17")
        self.assertEqual(fail_on["watch"]["CII"], ["BR-IP-05"])
        self.assertEqual(fail_on["watch"]["UBL"], ["BR-IG-05", "BR-IP-05"])

    def test_modes_without_ids_are_not_resolved(self) -> None:
        for raw in ("failed-assert", "never"):
            fail_on, warnings = validate.resolve_fail_on_ids(validate.parse_fail_on(raw), {}, "1.3.17")
            self.assertNotIn("watch", fail_on)
            self.assertEqual(warnings, [])

    def test_rename_table_maps_each_rule_to_the_same_rule(self) -> None:
        for syntax, renamed in validate.RENAMED_IN_1_3_17.items():
            old, new = rule_texts("1.3.16", syntax), rule_texts("1.3.17", syntax)
            self.assertEqual(len(renamed), 20)
            for before, after in renamed.items():
                self.assertNotIn(before, new, syntax)
                self.assertNotIn(after, old, syntax)
                self.assertEqual(old[before], new[after], f"{syntax} {before}->{after}")
        old, new = self.ids("1.3.16"), self.ids("1.3.17")
        for removed in validate.REMOVED_IN_1_3_17:
            self.assertIn(removed, old["CII"])
            self.assertNotIn(removed, new["CII"])

    def test_warning_line_shapes(self) -> None:
        message = "fail-on: BR-AF-05 was renamed"
        self.assertEqual(
            validate.warning_line(message, github=True),
            "::warning title=fail-on::fail-on: BR-AF-05 was renamed",
        )
        self.assertEqual(validate.warning_line(message, github=False), f"warning: {message}")


class FailOnRenamedEndToEndTests(unittest.TestCase):
    """An IGIC line fires BR-AF-* in 1.3.16, BR-IP-* (CII) / BR-IG-* (UBL) in 1.3.17."""

    def igic_invoices(self, tmp: Path) -> list[Path]:
        cii = (ROOT / "testdata" / "official" / "CII_example1.xml").read_text(encoding="utf-8")
        cii = cii.replace("<ram:CategoryCode>S</ram:CategoryCode>", "<ram:CategoryCode>L</ram:CategoryCode>", 1)
        ubl = (ROOT / "testdata" / "official" / "ubl-tc434-creditnote1.xml").read_text(encoding="utf-8")
        line = ubl.index("<cac:ClassifiedTaxCategory>")
        start = ubl.index("<cbc:ID>", line) + len("<cbc:ID>")
        ubl = ubl[:start] + "L" + ubl[ubl.index("</cbc:ID>", start):]
        paths = [tmp / "cii-igic.xml", tmp / "ubl-igic.xml"]
        paths[0].write_text(cii, encoding="utf-8")
        paths[1].write_text(ubl, encoding="utf-8")
        return paths

    def run_files(self, fail_on: str, version: str, *, github: bool = False) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            files = self.igic_invoices(Path(tmp))
            return validate.validate_files(
                files,
                syntax="auto",
                fail_on_raw=fail_on,
                version=version,
                root=ROOT,
                cwd=Path(tmp),
                emit_annotations=github,
            )

    def ids_by_file(self, result: dict) -> dict[str, list[str]]:
        return {row["path"]: row["failed_assert_ids"] for row in result["payload"]["files"]}

    def test_fixture_fires_the_renamed_rules(self) -> None:
        old = self.ids_by_file(self.run_files("never", "1.3.16"))
        new = self.ids_by_file(self.run_files("never", "1.3.17"))
        self.assertIn("BR-AF-01", old["cii-igic.xml"])
        self.assertIn("BR-AF-01", old["ubl-igic.xml"])
        self.assertIn("BR-IP-01", new["cii-igic.xml"])
        self.assertIn("BR-IG-01", new["ubl-igic.xml"])

    def test_old_id_still_fails_the_job_on_1_3_17(self) -> None:
        result = self.run_files("BR-AF-01", "1.3.17", github=True)
        self.assertEqual(result["exit_code"], validate.EXIT_FAIL)
        self.assertEqual(result["payload"]["verdict"], "fail")
        self.assertEqual(result["payload"]["fail_on"], "BR-AF-01")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(result["warnings"][0].startswith("::warning title=fail-on::fail-on: BR-AF-01"))
        self.assertIn("BR-IP-01 in CII and BR-IG-01 in UBL", result["warnings"][0])

    def test_filter_follows_the_syntax(self) -> None:
        # BR-IP-01 is the IGIC rule in CII but the IPSI rule in UBL: only the CII file trips it.
        result = self.run_files("BR-IP-01", "1.3.17")
        self.assertEqual(result["exit_code"], validate.EXIT_FAIL)
        self.assertEqual(result["warnings"], [])
        self.assertTrue(validate.file_triggers_fail(
            {"syntax": "CII", "failed_assert_ids": ["BR-IP-01"]},
            validate.resolve_fail_on_ids(
                validate.parse_fail_on("BR-AF-01"),
                validate.xslt_rule_ids(validate.load_xslt(ROOT, "1.3.17")),
                "1.3.17",
            )[0],
        ))
        self.assertFalse(validate.file_triggers_fail(
            {"syntax": "UBL", "failed_assert_ids": ["BR-IP-01"]},
            validate.resolve_fail_on_ids(
                validate.parse_fail_on("BR-AF-01"),
                validate.xslt_rule_ids(validate.load_xslt(ROOT, "1.3.17")),
                "1.3.17",
            )[0],
        ))

    def test_unwatched_rules_do_not_fail(self) -> None:
        result = self.run_files("BR-AG-01", "1.3.17")
        self.assertEqual(result["exit_code"], validate.EXIT_OK)
        self.assertEqual(len(result["warnings"]), 1)

    def test_main_prints_the_warning_and_exits_on_the_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            files = self.igic_invoices(Path(tmp))
            env = {"GITHUB_ACTIONS": "", "INPUT_VERSION": "", "INPUT_FAIL_ON": ""}
            with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(
                validate, "emit_line"
            ) as emit:
                code = validate.main(
                    ["--files", str(files[0]), "--fail-on", "BR-AF-01", "--version", "1.3.17"]
                )
        printed = [call.args[0] for call in emit.call_args_list]
        self.assertEqual(code, validate.EXIT_FAIL)
        self.assertTrue(any(line.startswith("warning: fail-on: BR-AF-01 was renamed") for line in printed))


if __name__ == "__main__":
    unittest.main()

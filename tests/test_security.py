#!/usr/bin/env python3
"""Security contract: Linux fail-closed, hardened parse, XSLT gate, annotations."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import validate  # noqa: E402

SECURITY = ROOT / "testdata" / "security"


class LinuxPreflightTests(unittest.TestCase):
    def test_darwin_refuses_without_saxon(self) -> None:
        with mock.patch.object(validate.sys, "platform", "darwin"):
            with mock.patch.dict(os.environ, {"EN16931_ALLOW_NON_LINUX": "1", "EN16931_FOO": "1"}):
                with self.assertRaises(validate.ConfigError) as ctx:
                    validate.require_linux()
        self.assertEqual(str(ctx.exception), validate.LINUX_ONLY_MESSAGE)

    def test_win32_refuses(self) -> None:
        with mock.patch.object(validate.sys, "platform", "win32"):
            with self.assertRaises(validate.ConfigError):
                validate.require_linux()

    def test_main_darwin_never_constructs_engine(self) -> None:
        with mock.patch.object(validate.sys, "platform", "darwin"):
            with mock.patch.object(validate, "SaxonEngine") as engine:
                rc = validate.main(["--files", "testdata/official/CII_example1.xml"])
        self.assertEqual(rc, validate.EXIT_CONFIG)
        engine.assert_not_called()


class HardenedParseTests(unittest.TestCase):
    def test_file_dtd_rejected_before_transform(self) -> None:
        with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
            with self.assertRaises(validate.ConfigError):
                validate.hardened_parse(SECURITY / "xxe-file-dtd.xml")
            transform.assert_not_called()

    def test_http_dtd_rejected_before_transform(self) -> None:
        with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
            with self.assertRaises(validate.ConfigError):
                validate.hardened_parse(SECURITY / "xxe-http-dtd.xml")
            transform.assert_not_called()

    def test_validate_files_does_not_hand_original_path_to_saxon(self) -> None:
        with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
            with self.assertRaises(validate.ConfigError):
                validate.validate_files(
                    [SECURITY / "xxe-file-dtd.xml"],
                    syntax="auto",
                    fail_on_raw="failed-assert",
                    version="1.3.16",
                    root=ROOT,
                    cwd=ROOT,
                )
        transform.assert_not_called()


def _write_xslt(tmp: Path, name: str, body: str) -> Path:
    dest = tmp / name
    dest.write_text(body, encoding="utf-8")
    return dest


class XsltGateTests(unittest.TestCase):
    def test_pinned_stylesheets_pass_the_gate(self) -> None:
        mapping = validate.load_xslt(ROOT, "1.3.16")
        self.assertEqual(mapping["CII"]["sha256"], validate.CII_XSLT_SHA256)
        self.assertEqual(mapping["UBL"]["sha256"], validate.UBL_XSLT_SHA256)

    def test_namespace_alias_include_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "alias-include.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:foo="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <foo:include href="outside.xslt"/>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_document_with_spaces_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "document-spaces.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="document ('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_doc_function_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "doc.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:copy-of select="doc('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_unparsed_text_lines_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "utl.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="unparsed-text-lines('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_source_document_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "source-doc.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/">
    <xsl:source-document href="https://example.invalid/x"><xsl:copy-of select="."/></xsl:source-document>
  </xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_result_document_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "result-doc.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/">
    <xsl:result-document href="/tmp/out.xml"><xsl:copy-of select="."/></xsl:result-document>
  </xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_json_doc_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "json-doc.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:copy-of select="json-doc('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_doc_available_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "doc-available.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="doc-available('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_unparsed_text_available_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "uta.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="unparsed-text-available('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_transform_function_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "transform.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:copy-of select="transform(map{'source-node':., 'stylesheet-location':'https://example.invalid/x.xsl'})"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_use_package_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "use-package.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:use-package name="https://example.invalid/pkg" package-version="1.0"/>
  <xsl:template match="/"><xsl:copy-of select="."/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_trace_is_rejected_without_logging_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "trace.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="trace(string(.), 'invoice')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
                with self.assertRaises(validate.EngineError) as ctx:
                    validate.assert_xslt_has_no_external_deps(dest)
            transform.assert_not_called()
            self.assertNotIn("LEAK_SENTINEL_4729", str(ctx.exception))

    def test_error_is_rejected_without_leaking_in_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "error.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="error(QName('', 'LEAK'), string(.))"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
                with self.assertRaises(validate.EngineError) as ctx:
                    validate.assert_xslt_has_no_external_deps(dest)
            transform.assert_not_called()
            self.assertNotIn("LEAK_SENTINEL_9912", str(ctx.exception))
            self.assertNotIn("string(.)", str(ctx.exception))

    def test_xsl_assert_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "assert.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/">
    <xsl:assert test="false()" select="string(.)"/>
  </xsl:template>
</xsl:stylesheet>
""",
            )
            with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
                with self.assertRaises(validate.EngineError):
                    validate.assert_xslt_has_no_external_deps(dest)
            transform.assert_not_called()

    def test_xsl_message_is_rejected_and_cannot_log_invoice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "message.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/">
    <xsl:message select="."/>
    <xsl:copy-of select="."/>
  </xsl:template>
</xsl:stylesheet>
""",
            )
            with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
                with self.assertRaises(validate.EngineError):
                    validate.assert_xslt_has_no_external_deps(dest)
                transform.assert_not_called()

    def test_xsl_evaluate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "evaluate.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/">
    <xsl:evaluate xpath="'doc(&quot;https://example.invalid/x&quot;)'"/>
  </xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def test_uri_collection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(
                Path(tmp),
                "uri-col.xslt",
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:copy-of select="uri-collection('https://example.invalid/x')"/></xsl:template>
</xsl:stylesheet>
""",
            )
            with self.assertRaises(validate.EngineError):
                validate.assert_xslt_has_no_external_deps(dest)

    def _assert_ref_blocked(self, body: str, sentinel: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _write_xslt(Path(tmp), "ref-mutant.xslt", body)
            stdout, stderr = StringIO(), StringIO()
            with mock.patch.object(validate.SaxonEngine, "transform", autospec=True) as transform:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(validate.EngineError) as ctx:
                        validate.assert_xslt_has_no_external_deps(dest)
            transform.assert_not_called()
            blob = "\n".join([str(ctx.exception), stdout.getvalue(), stderr.getvalue()])
            self.assertNotIn(sentinel, blob)

    def test_trace_function_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="let $f := trace#2 return $f(string(.), 'LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_error_function_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="let $f := error#1 return $f('LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_doc_function_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:copy-of select="let $f := doc#1 return $f('/etc/passwd')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_function_lookup_trace_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="function-lookup(QName('http://www.w3.org/2005/xpath-functions', 'trace'), 2)(string(.), 'LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_prefixed_trace_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:fn="http://www.w3.org/2005/xpath-functions" version="3.0">
  <xsl:template match="/"><xsl:value-of select="fn:trace#2(string(.), 'LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_prefixed_function_lookup_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:fn="http://www.w3.org/2005/xpath-functions" version="3.0">
  <xsl:template match="/"><xsl:value-of select="fn:function-lookup(QName('http://www.w3.org/2005/xpath-functions', 'error'), 1)('LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_eqname_error_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="Q{http://www.w3.org/2005/xpath-functions}error#1('LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_function_lookup_ref_is_rejected(self) -> None:
        self._assert_ref_blocked(
            """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:template match="/"><xsl:value-of select="let $f := function-lookup#2 return $f(QName('http://www.w3.org/2005/xpath-functions', 'trace'), 2)(string(.), 'LEAK_SENTINEL_7788')"/></xsl:template>
</xsl:stylesheet>
""",
            "LEAK_SENTINEL_7788",
        )

    def test_vendor_stylesheets_remain_accepted(self) -> None:
        mapping = validate.load_xslt(ROOT, "1.3.16")
        self.assertEqual(mapping["CII"]["sha256"], validate.CII_XSLT_SHA256)
        self.assertEqual(mapping["UBL"]["sha256"], validate.UBL_XSLT_SHA256)
        validate.assert_xslt_has_no_external_deps(ROOT / validate.CII_XSLT_REL)
        validate.assert_xslt_has_no_external_deps(ROOT / validate.UBL_XSLT_REL)


class FilesNewlineTests(unittest.TestCase):
    def test_comma_is_opaque_not_a_separator(self) -> None:
        pieces = validate.split_patterns(["a.xml,b.xml"])
        self.assertEqual(pieces, ["a.xml,b.xml"])

    def test_newline_splits_two_patterns(self) -> None:
        pieces = validate.split_patterns(["a.xml\nb.xml"])
        self.assertEqual(pieces, ["a.xml", "b.xml"])

    def test_edge_spaces_are_kept_in_the_pattern(self) -> None:
        pieces = validate.split_patterns(["  a.xml  \n\nb.xml"])
        self.assertEqual(pieces, ["  a.xml  ", "b.xml"])

    def test_space_in_filename_is_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = ROOT / "testdata" / "official" / "CII_example1.xml"
            spaced = tmp_path / "has space.xml"
            shutil.copyfile(src, spaced)
            other = tmp_path / "other.xml"
            shutil.copyfile(src, other)
            files = validate.expand_files(
                [str(spaced) + "\n" + str(other)],
                cwd=tmp_path,
            )
            names = sorted(path.name for path in files)
            self.assertEqual(names, ["has space.xml", "other.xml"])

    def test_leading_space_filename_matches_untrimmed_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = ROOT / "testdata" / "official" / "CII_example1.xml"
            spaced = tmp_path / " has space.xml"
            shutil.copyfile(src, spaced)
            files = validate.expand_files([" has space.xml"], cwd=tmp_path)
            self.assertEqual([path.name for path in files], [" has space.xml"])


class AnnotationEscapeTests(unittest.TestCase):
    def test_reserved_characters_are_encoded_once(self) -> None:
        line = validate.annotation_line(
            "invoices/a,b:c.xml",
            {"id": "BR:1", "text": "50% done\r\nnext, item"},
        )
        self.assertEqual(line.count("\n"), 0)
        self.assertNotIn("\r", line)
        self.assertIn("file=invoices/a%2Cb%3Ac.xml", line)
        self.assertIn("title=BR%3A1", line)
        self.assertIn("%25", line)
        self.assertIn("%0D", line)
        self.assertIn("%0A", line)
        self.assertTrue(line.startswith("::error "))
        self.assertEqual(line.count("::"), 2)

    def test_truncation_happens_before_percent_encode(self) -> None:
        text = "%" * 250
        encoded = validate.encode_workflow_msg(text)
        self.assertTrue(encoded.endswith("..."))
        self.assertFalse(encoded.endswith("%"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""C gate: refuse any DOCTYPE before Saxon. Local-runnable, not Action globs."""

from __future__ import annotations

import hashlib
import http.server
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import validate  # noqa: E402

CII_MIN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<rsm:CrossIndustryInvoice xmlns:rsm="{validate.CII_NS}">x'
    "</rsm:CrossIndustryInvoice>\n"
)
CANARY_TOKEN = "CANARY_DTD_GATE_9f2c1a70"


class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def _start_hit_server(hits: list[str]) -> tuple[ReuseTCPServer, str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.end_headers()
            self.wfile.write(b"<!ELEMENT foo (#PCDATA)>\n")

        def log_message(self, *_args) -> None:
            return None

    httpd = ReuseTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    return httpd, f"http://{host}:{port}"


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def transform(self, xml_path: Path, xslt_path: Path) -> str:
        self.calls.append(Path(xml_path))
        return '<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl"/>'

    def close(self) -> None:
        return None


def _run_validate(path: Path, engine: FakeEngine) -> dict:
    tmp_root = path.parent

    def fake_load_xslt(root: Path, version: str) -> dict:
        dummy = tmp_root / "dummy.xslt"
        dummy.write_text("<xsl:stylesheet version='3.0' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>\n")
        return {
            "CII": {"path": dummy, "sha256": "0", "logical": "dummy"},
            "UBL": {"path": dummy, "sha256": "0", "logical": "dummy"},
        }

    orig_engine = validate.SaxonEngine
    orig_load = validate.load_xslt
    validate.SaxonEngine = lambda: engine  # type: ignore[misc, assignment]
    validate.load_xslt = fake_load_xslt  # type: ignore[assignment]
    try:
        return validate.validate_files(
            [path],
            syntax="auto",
            fail_on_raw="never",
            version="1.3.16",
            root=tmp_root,
            cwd=tmp_root,
        )
    finally:
        validate.SaxonEngine = orig_engine
        validate.load_xslt = orig_load


class GateDoctypeTests(unittest.TestCase):
    def test_no_dtd_parse_ok(self) -> None:
        data = CII_MIN.encode("utf-8")
        before = hashlib.sha256(data).hexdigest()
        validate.gate_doctype(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), before)

    def test_internal_doctype_refused(self) -> None:
        data = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE foo [ <!ENTITY x "INTERNAL"> ]>\n'
            b"<foo>&x;</foo>\n"
        )
        with self.assertRaises(validate.DtdRefused):
            validate.gate_doctype(data)

    def test_system_file_refused_without_reading_canary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dtd-file-") as td:
            folder = Path(td)
            canary = folder / "canary.txt"
            canary.write_bytes(CANARY_TOKEN.encode("ascii"))
            invoice = folder / "system-file.xml"
            invoice.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{canary.as_uri()}"> ]>\n'
                "<foo>&ext;</foo>\n",
                encoding="utf-8",
                newline="\n",
            )
            original_mode = canary.stat().st_mode
            try:
                os.chmod(canary, 0)
            except OSError:
                pass
            try:
                with self.assertRaises(validate.ConfigError) as ctx:
                    validate.refuse_invoice_dtd(invoice)
            finally:
                try:
                    os.chmod(canary, original_mode)
                except OSError:
                    pass
            message = str(ctx.exception)
            self.assertIn("DOCTYPE", message)
            self.assertNotIn("well-formed", message)
            self.assertEqual(canary.read_bytes().decode("ascii"), CANARY_TOKEN)

    def test_system_http_refused_zero_get(self) -> None:
        hits: list[str] = []
        httpd, base = _start_hit_server(hits)
        try:
            data = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{base}/entity"> ]>\n'
                "<foo>&ext;</foo>\n"
            ).encode("utf-8")
            with self.assertRaises(validate.DtdRefused):
                validate.gate_doctype(data)
            self.assertEqual(hits, [])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_public_doctype_refused_zero_get(self) -> None:
        hits: list[str] = []
        httpd, base = _start_hit_server(hits)
        try:
            data = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<!DOCTYPE foo PUBLIC "-//TEST//DTD Foo 1.0//EN" "{base}/public.dtd">\n'
                "<foo>ok</foo>\n"
            ).encode("utf-8")
            with self.assertRaises(validate.DtdRefused):
                validate.gate_doctype(data)
            self.assertEqual(hits, [])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_official_fixtures_have_no_doctype(self) -> None:
        official = ROOT / "testdata" / "official"
        mutants = ROOT / "testdata" / "mutants"
        xml_files = sorted(official.glob("*.xml")) + sorted(mutants.glob("*.xml"))
        if len(xml_files) != 20:
            self.skipTest("official/mutant fixtures not present")
        for path in xml_files:
            raw = path.read_bytes()
            validate.gate_doctype(raw)
            self.assertNotIn(b"<!DOCTYPE", raw.upper())
            self.assertNotIn(b"<!ENTITY", raw.upper())


class ValidateFilesGateTests(unittest.TestCase):
    def test_no_dtd_control_reaches_saxon_same_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dtd-ok-") as td:
            path = Path(td) / "control.xml"
            payload = CII_MIN.encode("utf-8")
            path.write_bytes(payload)
            engine = FakeEngine()
            result = _run_validate(path, engine)
            self.assertEqual(len(engine.calls), 1)
            self.assertEqual(engine.calls[0].resolve(), path.resolve())
            self.assertEqual(
                result["payload"]["files"][0]["sha256"],
                hashlib.sha256(payload).hexdigest(),
            )
            self.assertEqual(path.read_bytes(), payload)

    def test_internal_doctype_never_reaches_saxon(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dtd-int-") as td:
            path = Path(td) / "internal.xml"
            path.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE rsm:CrossIndustryInvoice [ <!ENTITY x "INTERNAL"> ]>\n'
                f'<rsm:CrossIndustryInvoice xmlns:rsm="{validate.CII_NS}">&x;'
                "</rsm:CrossIndustryInvoice>\n",
                encoding="utf-8",
                newline="\n",
            )
            engine = FakeEngine()
            with self.assertRaises(validate.ConfigError) as ctx:
                _run_validate(path, engine)
            self.assertIn("DOCTYPE", str(ctx.exception))
            self.assertEqual(engine.calls, [])

    def test_system_file_never_reads_canary_never_reaches_saxon(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dtd-sysfile-") as td:
            folder = Path(td)
            canary = folder / "canary.txt"
            canary.write_bytes(CANARY_TOKEN.encode("ascii"))
            path = folder / "system-file.xml"
            path.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<!DOCTYPE rsm:CrossIndustryInvoice SYSTEM "{canary.as_uri()}">\n'
                f'<rsm:CrossIndustryInvoice xmlns:rsm="{validate.CII_NS}">x'
                "</rsm:CrossIndustryInvoice>\n",
                encoding="utf-8",
                newline="\n",
            )
            original_mode = canary.stat().st_mode
            try:
                os.chmod(canary, 0)
            except OSError:
                pass
            engine = FakeEngine()
            try:
                with self.assertRaises(validate.ConfigError) as ctx:
                    _run_validate(path, engine)
            finally:
                try:
                    os.chmod(canary, original_mode)
                except OSError:
                    pass
            self.assertIn("DOCTYPE", str(ctx.exception))
            self.assertEqual(engine.calls, [])
            self.assertEqual(canary.read_bytes().decode("ascii"), CANARY_TOKEN)

    def test_system_http_zero_get_never_reaches_saxon(self) -> None:
        hits: list[str] = []
        httpd, base = _start_hit_server(hits)
        try:
            with tempfile.TemporaryDirectory(prefix="dtd-http-") as td:
                path = Path(td) / "system-http.xml"
                path.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<!DOCTYPE rsm:CrossIndustryInvoice SYSTEM "{base}/entity">\n'
                    f'<rsm:CrossIndustryInvoice xmlns:rsm="{validate.CII_NS}">x'
                    "</rsm:CrossIndustryInvoice>\n",
                    encoding="utf-8",
                    newline="\n",
                )
                engine = FakeEngine()
                with self.assertRaises(validate.ConfigError) as ctx:
                    _run_validate(path, engine)
                self.assertIn("DOCTYPE", str(ctx.exception))
                self.assertEqual(engine.calls, [])
                self.assertEqual(hits, [])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_public_zero_get_never_reaches_saxon(self) -> None:
        hits: list[str] = []
        httpd, base = _start_hit_server(hits)
        try:
            with tempfile.TemporaryDirectory(prefix="dtd-pub-") as td:
                path = Path(td) / "public.xml"
                path.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<!DOCTYPE rsm:CrossIndustryInvoice PUBLIC "-//TEST//DTD 1.0//EN" "{base}/public.dtd">\n'
                    f'<rsm:CrossIndustryInvoice xmlns:rsm="{validate.CII_NS}">x'
                    "</rsm:CrossIndustryInvoice>\n",
                    encoding="utf-8",
                    newline="\n",
                )
                engine = FakeEngine()
                with self.assertRaises(validate.ConfigError) as ctx:
                    _run_validate(path, engine)
                self.assertIn("DOCTYPE", str(ctx.exception))
                self.assertEqual(engine.calls, [])
                self.assertEqual(hits, [])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()

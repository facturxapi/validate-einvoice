#!/usr/bin/env python3
"""C gate: stdlib expat fail-closed on DOCTYPE. B is extra HTTP block only."""

from __future__ import annotations

import hashlib
import http.server
import inspect
import os
import socketserver
import subprocess
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

CANARY = "CANARY_DTD_GATE_TOKEN_7c1e9b42"

try:
    from saxonche import PySaxonProcessor

    HAS_SAXON = True
except ImportError:
    HAS_SAXON = False


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HitHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str]

    def do_GET(self):
        self.hits.append(self.path)
        body = b"HTTP_PROBE_BODY_OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


def start_http() -> tuple[ThreadingHTTPServer, list[str]]:
    hits: list[str] = []

    class BoundHandler(HitHandler):
        pass

    BoundHandler.hits = hits
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, hits


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_xsl(dir_path: Path) -> Path:
    path = dir_path / "dump.xsl"
    path.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:output method="text"/>
  <xsl:template match="/"><xsl:value-of select="string(.)"/></xsl:template>
</xsl:stylesheet>
"""
    )
    return path


def current_saxon_dump(xml_path: Path, xslt_path: Path) -> str:
    """Unpatched SaxonC path: source_file= with no C gate and no B knob."""
    proc = PySaxonProcessor(license=False)
    xslt = proc.new_xslt30_processor()
    executable = xslt.compile_stylesheet(stylesheet_file=str(xslt_path))
    if executable is None:
        raise RuntimeError("CURRENT compile failed")
    out = executable.transform_to_string(source_file=str(xml_path))
    closer = getattr(proc, "release", None)
    if callable(closer):
        closer()
    return "" if out is None else out


def current_saxon_dump_subprocess(
    xml_path: Path, xslt_path: Path, *, timeout: float = 12.0
) -> subprocess.CompletedProcess[str]:
    """CURRENT Saxon in a child process so a hung HTTP fetch cannot stall tests."""
    script = (
        "from saxonche import PySaxonProcessor\n"
        "import sys\n"
        "xml_path, xslt_path = sys.argv[1], sys.argv[2]\n"
        "proc = PySaxonProcessor(license=False)\n"
        "xslt = proc.new_xslt30_processor()\n"
        "exe = xslt.compile_stylesheet(stylesheet_file=xslt_path)\n"
        "out = exe.transform_to_string(source_file=xml_path)\n"
        "sys.stdout.write('' if out is None else out)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script, str(xml_path), str(xslt_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class GateMechanismTests(unittest.TestCase):
    def test_gate_is_expat_start_doctype_not_regex_or_iterparse(self) -> None:
        src = inspect.getsource(validate.gate_invoice_bytes)
        module = Path(validate.__file__).read_text(encoding="utf-8")
        self.assertIn("StartDoctypeDeclHandler", src)
        self.assertIn("xml.parsers.expat", module)
        self.assertNotIn("re.search", src)
        self.assertNotIn("re.match", src)
        self.assertNotIn("iterparse", src)
        detect = inspect.getsource(validate.detect_syntax)
        self.assertIn("ET.iterparse", detect)
        self.assertNotIn("StartDoctypeDeclHandler", detect)

    def test_b_allowed_protocols_is_file_not_empty(self) -> None:
        self.assertEqual(validate.SAXON_ALLOWED_PROTOCOLS_VALUE, "file")
        self.assertNotEqual(validate.SAXON_ALLOWED_PROTOCOLS_VALUE, "")
        src = inspect.getsource(validate.SaxonEngine.__init__)
        self.assertIn("SAXON_ALLOWED_PROTOCOLS_FEATURE", src)
        self.assertIn("SAXON_ALLOWED_PROTOCOLS_VALUE", src)
        self.assertNotIn('""', src.split("set_configuration_property")[1][:400])


class GateAdversarialTests(unittest.TestCase):
    def test_no_dtd_control_parse_ok_bytes_unchanged(self) -> None:
        data = b'<?xml version="1.0" encoding="UTF-8"?>\n<foo>NO_DTD_CONTROL</foo>\n'
        before = sha256_bytes(data)
        validate.gate_invoice_bytes(data, name="no-dtd.xml")
        self.assertEqual(sha256_bytes(data), before)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no-dtd.xml"
            path.write_bytes(data)
            digest = validate.gate_invoice_path(path)
            self.assertEqual(digest, before)
            self.assertEqual(sha256_bytes(path.read_bytes()), before)

    def test_internal_doctype_refused_bytes_unchanged(self) -> None:
        data = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE foo [ <!ENTITY x "INTERNAL_ENTITY_VALUE"> ]>\n'
            b"<foo>&x;</foo>\n"
        )
        before = sha256_bytes(data)
        with self.assertRaises(validate.DtdRefused) as ctx:
            validate.gate_invoice_bytes(data, name="internal-dtd.xml")
        self.assertEqual(ctx.exception.doctype_name, "foo")
        self.assertTrue(ctx.exception.has_internal_subset)
        self.assertEqual(sha256_bytes(data), before)
        self.assertNotIn(b"INTERNAL_ENTITY_VALUE", str(ctx.exception).encode("utf-8"))

    def test_system_file_refused_zero_canary_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary = base / "canary.txt"
            canary.write_bytes((CANARY + "\n").encode("ascii"))
            uri = canary.resolve().as_uri()
            data = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{uri}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            xml_path = base / "system-file.xml"
            xml_path.write_bytes(data)
            before = sha256_bytes(xml_path.read_bytes())
            httpd, hits = start_http()
            try:
                with self.assertRaises(validate.DtdRefused) as ctx:
                    validate.gate_invoice_path(xml_path)
            finally:
                httpd.shutdown()
            self.assertEqual(hits, [])
            self.assertNotIn(CANARY, str(ctx.exception))
            self.assertEqual(sha256_bytes(xml_path.read_bytes()), before)
            self.assertEqual(canary.read_bytes(), (CANARY + "\n").encode("ascii"))

    def test_system_http_refused_zero_get(self) -> None:
        httpd, hits = start_http()
        try:
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/entity"
            data = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{url}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            before = sha256_bytes(data)
            with self.assertRaises(validate.DtdRefused):
                validate.gate_invoice_bytes(data, name="system-http.xml")
            self.assertEqual(hits, [])
            self.assertEqual(sha256_bytes(data), before)
        finally:
            httpd.shutdown()

    def test_public_http_refused_zero_get(self) -> None:
        httpd, hits = start_http()
        try:
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/public.dtd"
            data = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + (
                    f'<!DOCTYPE foo PUBLIC "-//GATE//DTD Foo 1.0//EN" "{url}">\n'
                ).encode("ascii")
                + b"<foo>ok</foo>\n"
            )
            with self.assertRaises(validate.DtdRefused) as ctx:
                validate.gate_invoice_bytes(data, name="public-http.xml")
            self.assertEqual(hits, [])
            self.assertEqual(ctx.exception.pubid, "-//GATE//DTD Foo 1.0//EN")
        finally:
            httpd.shutdown()

    def test_external_dtd_system_file_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dtd = Path(tmp) / "ext.dtd"
            dtd.write_bytes(b"<!ELEMENT foo (#PCDATA)>\n")
            uri = dtd.resolve().as_uri()
            data = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo SYSTEM "{uri}">\n'.encode("ascii")
                + b"<foo>ok</foo>\n"
            )
            with self.assertRaises(validate.DtdRefused) as ctx:
                validate.gate_invoice_bytes(data, name="external-dtd-file.xml")
            self.assertFalse(ctx.exception.has_internal_subset)
            self.assertIsNotNone(ctx.exception.sysid)

    def test_et_iterparse_is_not_the_gate(self) -> None:
        data = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE foo [ <!ENTITY x "INTERNAL_ENTITY_VALUE"> ]>\n'
            b"<foo>&x;</foo>\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "internal-dtd.xml"
            path.write_bytes(data)
            import xml.etree.ElementTree as ET

            root_tag = None
            for _event, elem in ET.iterparse(path, events=("start",)):
                root_tag = elem.tag
                break
            self.assertEqual(root_tag, "foo")
            with self.assertRaises(validate.DtdRefused):
                validate.gate_invoice_path(path)


@unittest.skipUnless(HAS_SAXON, "saxonche not installed")
class BeforeAfterSaxonTests(unittest.TestCase):
    def test_current_follows_system_http_after_c_zero_get(self) -> None:
        httpd, hits = start_http()
        try:
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/entity"
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                xml_path = base / "system-http.xml"
                xml_path.write_bytes(
                    b'<?xml version="1.0" encoding="UTF-8"?>\n'
                    + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{url}"> ]>\n'.encode(
                        "ascii"
                    )
                    + b"<foo>&ext;</foo>\n"
                )
                xslt_path = dump_xsl(base)
                hits.clear()
                try:
                    current_saxon_dump_subprocess(xml_path, xslt_path, timeout=12.0)
                except subprocess.TimeoutExpired:
                    pass
                if sys.platform.startswith("linux"):
                    self.assertTrue(hits, msg="CURRENT must follow SYSTEM HTTP")
                hits.clear()
                with self.assertRaises(validate.DtdRefused):
                    validate.gate_invoice_path(xml_path)
                self.assertEqual(hits, [])
                hits.clear()
                with self.assertRaises(validate.DtdRefused):
                    engine = validate.SaxonEngine()
                    try:
                        engine.transform(xml_path, xslt_path)
                    finally:
                        engine.close()
                self.assertEqual(hits, [])
        finally:
            httpd.shutdown()

    def test_current_follows_system_file_after_c_no_canary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary = base / "canary.txt"
            canary.write_bytes((CANARY + "\n").encode("ascii"))
            uri = canary.resolve().as_uri()
            xml_path = base / "system-file.xml"
            xml_path.write_bytes(
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{uri}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            xslt_path = dump_xsl(base)
            current_out = current_saxon_dump(xml_path, xslt_path)
            if sys.platform.startswith("linux"):
                self.assertIn(CANARY, current_out)
            with self.assertRaises(validate.DtdRefused) as ctx:
                validate.gate_invoice_path(xml_path)
            self.assertNotIn(CANARY, str(ctx.exception))
            with self.assertRaises(validate.DtdRefused):
                engine = validate.SaxonEngine()
                try:
                    engine.transform(xml_path, xslt_path)
                finally:
                    engine.close()


@unittest.skipUnless(HAS_SAXON, "saxonche not installed")
class OfficialBytesAndCrlfTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["EN16931_ACTION_ROOT"] = str(ROOT)

    def test_official_file_same_sha_before_after_gate_and_report(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example3.xml"
        before = sha256_bytes(path.read_bytes())
        digest = validate.gate_invoice_path(path)
        after = sha256_bytes(path.read_bytes())
        self.assertEqual(digest, before)
        self.assertEqual(after, before)
        result = validate.validate_files(
            [path],
            syntax="auto",
            fail_on_raw="failed-assert",
            version="1.3.16",
            root=ROOT,
            cwd=ROOT,
        )
        row = result["payload"]["files"][0]
        self.assertEqual(row["sha256"], before)
        self.assertEqual(row["sha256"], validate.sha256_file(path))
        self.assertEqual(row["verdict"], "pass")

    def test_crlf_user_invoice_hashed_as_crlf(self) -> None:
        raw = (ROOT / "testdata" / "official" / "CII_example3.xml").read_bytes()
        self.assertNotIn(b"\r", raw)
        crlf = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        self.assertIn(b"\r\n", crlf)
        self.assertNotEqual(sha256_bytes(raw), sha256_bytes(crlf))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user-crlf.xml"
            path.write_bytes(crlf)
            self.assertEqual(validate.sha256_file(path), sha256_bytes(crlf))
            digest = validate.gate_invoice_path(path)
            self.assertEqual(digest, sha256_bytes(crlf))
            self.assertEqual(sha256_bytes(path.read_bytes()), sha256_bytes(crlf))
            result = validate.validate_files(
                [path],
                syntax="auto",
                fail_on_raw="failed-assert",
                version="1.3.16",
                root=ROOT,
                cwd=Path(tmp),
            )
            row = result["payload"]["files"][0]
            self.assertEqual(row["sha256"], sha256_bytes(crlf))
            self.assertNotEqual(row["sha256"], sha256_bytes(raw))

    def test_validate_files_refuses_doctype_before_saxon(self) -> None:
        httpd, hits = start_http()
        try:
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/entity"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "hostile.xml"
                path.write_bytes(
                    b'<?xml version="1.0" encoding="UTF-8"?>\n'
                    + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{url}"> ]>\n'.encode(
                        "ascii"
                    )
                    + b"<foo>&ext;</foo>\n"
                )
                with self.assertRaises(validate.DtdRefused):
                    validate.validate_files(
                        [path],
                        syntax="auto",
                        fail_on_raw="failed-assert",
                        version="1.3.16",
                        root=ROOT,
                    )
                self.assertEqual(hits, [])
        finally:
            httpd.shutdown()

    def test_b_does_not_break_stylesheet_file_and_source_file(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example3.xml"
        engine = validate.SaxonEngine()
        try:
            xslt = ROOT / validate.CII_XSLT_REL
            svrl = engine.transform(path, xslt)
        finally:
            engine.close()
        self.assertIn("schematron-output", svrl)


class FifoNotOpenedTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "named pipe is POSIX")
    def test_system_fifo_not_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fifo = base / "canary.fifo"
            os.mkfifo(fifo)
            uri = fifo.resolve().as_uri()
            data = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{uri}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            xml_path = base / "system-fifo.xml"
            xml_path.write_bytes(data)
            with self.assertRaises(validate.DtdRefused):
                validate.gate_invoice_path(xml_path)


if __name__ == "__main__":
    unittest.main()

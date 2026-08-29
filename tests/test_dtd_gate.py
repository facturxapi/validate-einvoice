#!/usr/bin/env python3
"""C gate: stdlib expat fail-closed on DOCTYPE. B is extra HTTP block only."""

from __future__ import annotations

import contextlib
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
import warnings
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
    block_on_close = False


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


@contextlib.contextmanager
def http_probe_server(*, join_timeout: float = 5.0):
    """One HTTP probe used by SYSTEM tests and the B document() probe.

    Start in a thread; always shutdown, server_close, join; assert stopped.
    """
    hits: list[str] = []

    class BoundHandler(HitHandler):
        pass

    BoundHandler.hits = hits
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
    thread = threading.Thread(
        target=httpd.serve_forever, name="ve-http-probe", daemon=True
    )
    thread.start()
    try:
        yield httpd, hits
    finally:
        try:
            httpd.shutdown()
        finally:
            httpd.server_close()
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                raise AssertionError("http probe thread still running")


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


def document_http_xsl(dir_path: Path) -> Path:
    """Test-only stylesheet: document($uri). Not under testdata/ official or mutants."""
    path = dir_path / "document-http.xsl"
    path.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="3.0">
  <xsl:output method="text"/>
  <xsl:param name="uri" required="yes"/>
  <xsl:template match="/"><xsl:value-of select="document($uri)"/></xsl:template>
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
        self.assertIn("io.BytesIO", detect)
        self.assertNotIn("StartDoctypeDeclHandler", detect)
        snap_src = inspect.getsource(validate.SaxonEngine.transform_snapshot)
        self.assertIn("private_snapshot_file", snap_src)
        self.assertIn("source_file=str(snap)", snap_src)
        self.assertNotIn("xml_path", snap_src)
        t_src = inspect.getsource(validate.SaxonEngine.transform)
        self.assertIn("transform_snapshot", t_src)
        self.assertNotIn("source_file=", t_src)
        vf_src = inspect.getsource(validate.validate_files)
        self.assertIn("transform_snapshot", vf_src)
        self.assertNotIn("engine.transform(path", vf_src)

    def test_b_allowed_protocols_is_file_not_empty(self) -> None:
        self.assertEqual(validate.SAXON_ALLOWED_PROTOCOLS_VALUE, "file")
        self.assertNotEqual(validate.SAXON_ALLOWED_PROTOCOLS_VALUE, "")
        src = inspect.getsource(validate.SaxonEngine.__init__)
        self.assertIn("SAXON_ALLOWED_PROTOCOLS_FEATURE", src)
        self.assertIn("SAXON_ALLOWED_PROTOCOLS_VALUE", src)
        self.assertNotIn('""', src.split("set_configuration_property")[1][:400])

    def test_snapshot_cleanup_is_verified_not_swallowed(self) -> None:
        src = inspect.getsource(validate.cleanup_private_snapshot)
        self.assertIn("SnapshotCleanupError", src)
        self.assertIn("exists()", src)
        helper = inspect.getsource(validate.remove_with_retry)
        self.assertIn("attempts", helper)
        snap_src = inspect.getsource(validate.private_snapshot_file)
        self.assertNotIn("always delete", snap_src)
        self.assertIn("cleanup_private_snapshot", snap_src)
        close_src = inspect.getsource(validate.SaxonEngine.close)
        self.assertIn("gc.collect", close_src)


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
            with http_probe_server() as (_httpd, hits):
                with self.assertRaises(validate.DtdRefused) as ctx:
                    validate.gate_invoice_path(xml_path)
            self.assertEqual(hits, [])
            self.assertNotIn(CANARY, str(ctx.exception))
            self.assertEqual(sha256_bytes(xml_path.read_bytes()), before)
            self.assertEqual(canary.read_bytes(), (CANARY + "\n").encode("ascii"))

    def test_system_http_refused_zero_get(self) -> None:
        with http_probe_server() as (httpd, hits):
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

    def test_public_http_refused_zero_get(self) -> None:
        with http_probe_server() as (httpd, hits):
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
        with http_probe_server() as (httpd, hits):
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
        with http_probe_server() as (httpd, hits):
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

    def test_b_does_not_break_stylesheet_file_and_source_file(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example3.xml"
        engine = validate.SaxonEngine()
        try:
            xslt = ROOT / validate.CII_XSLT_REL
            svrl = engine.transform(path, xslt)
        finally:
            engine.close()
        self.assertIn("schematron-output", svrl)




class PrivateSnapshotFileTests(unittest.TestCase):
    def test_snapshot_preserves_crlf_and_is_not_user_path(self) -> None:
        data = b"<a>\r\n</a>\n"
        lf = b"<a>\n</a>\n"
        self.assertNotEqual(sha256_bytes(data), sha256_bytes(lf))
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.xml"
            user.write_bytes(data)
            yielded: Path | None = None
            with validate.private_snapshot_file(data) as snap:
                yielded = snap
                self.assertTrue(snap.exists())
                self.assertEqual(snap.read_bytes(), data)
                self.assertNotEqual(snap.resolve(), user.resolve())
                if os.name != "nt":
                    mode = os.stat(snap.parent).st_mode & 0o777
                    self.assertEqual(mode, 0o700)
            assert yielded is not None
            self.assertFalse(yielded.exists())
            self.assertFalse(yielded.parent.exists())
            self.assertEqual(user.read_bytes(), data)

    def test_normal_cleanup_snapshot_gone_after_use(self) -> None:
        data = b"<foo>SNAPSHOT_CLEANUP_CONTROL</foo>\n"
        yielded: Path | None = None
        parent: Path | None = None
        with validate.private_snapshot_file(data) as snap:
            yielded = snap
            parent = snap.parent
            self.assertTrue(snap.exists())
            self.assertTrue(parent.exists())
            self.assertEqual(snap.read_bytes(), data)
        assert yielded is not None
        assert parent is not None
        self.assertFalse(yielded.exists())
        self.assertFalse(parent.exists())

    def test_unlink_always_fails_raises_cleanup_error_no_paths_in_message(self) -> None:
        secret = b"<foo>INVOICE_SECRET_BYTES_9f3a</foo>\n"
        calls: list[str] = []
        sleeps: list[float] = []

        def boom(path: str) -> None:
            calls.append(path)
            raise OSError("simulated unlink failure")

        yielded: Path | None = None
        try:
            with self.assertRaises(validate.SnapshotCleanupError) as ctx:
                with validate.private_snapshot_file(
                    secret,
                    attempts=4,
                    delay=0.0,
                    sleeper=sleeps.append,
                    unlinker=boom,
                ) as snap:
                    yielded = snap
                    self.assertTrue(snap.exists())
            assert yielded is not None
            self.assertEqual(len(calls), 4)
            self.assertTrue(sleeps)
            self.assertTrue(all(s == 0.0 for s in sleeps))
            msg = str(ctx.exception)
            self.assertNotIn(str(yielded), msg)
            self.assertNotIn(str(yielded.parent), msg)
            self.assertNotIn("INVOICE_SECRET_BYTES_9f3a", msg)
            self.assertNotIn("ve-snap-", msg)
            self.assertIsInstance(ctx.exception, validate.EngineError)
        finally:
            if yielded is not None:
                try:
                    os.unlink(yielded)
                except OSError:
                    pass
                try:
                    os.rmdir(yielded.parent)
                except OSError:
                    pass

    def test_unlink_fails_once_then_succeeds(self) -> None:
        data = b"<foo>RETRY_THEN_OK</foo>\n"
        real_unlink = os.unlink
        state = {"n": 0}

        def flaky(path: str) -> None:
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("first unlink fails")
            real_unlink(path)

        sleeps: list[float] = []
        yielded: Path | None = None
        with validate.private_snapshot_file(
            data,
            attempts=4,
            delay=0.0,
            sleeper=sleeps.append,
            unlinker=flaky,
        ) as snap:
            yielded = snap
            self.assertTrue(snap.exists())
        assert yielded is not None
        self.assertGreaterEqual(state["n"], 2)
        self.assertTrue(sleeps)
        self.assertFalse(yielded.exists())
        self.assertFalse(yielded.parent.exists())


class HttpProbeServerTests(unittest.TestCase):
    def test_context_manager_stops_thread_no_resource_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with http_probe_server() as (httpd, hits):
                host, port = httpd.server_address[:2]
                import urllib.request

                with urllib.request.urlopen(
                    f"http://{host}:{port}/ping", timeout=2
                ) as resp:
                    resp.read()
                self.assertEqual(hits, ["/ping"])
            import gc as _gc

            _gc.collect()
            sock_warns = [
                w for w in caught if issubclass(w.category, ResourceWarning)
            ]
            self.assertEqual(sock_warns, [])


def anti_pattern_transform_user_path(
    xml_path: Path,
    xslt_path: Path,
    *,
    after_gate=None,
) -> str:
    """ANTI-PATTERN: gate the user path, then source_file= that same path.

    Same-path is not same-bytes: a replace after the gate is visible to Saxon.
    Production must transform a private snapshot instead. Kept as the
    documented old design for the FAIL proof below.
    """
    validate.gate_invoice_path(xml_path)
    if after_gate is not None:
        after_gate(xml_path)
    return current_saxon_dump(xml_path, xslt_path)


@unittest.skipUnless(HAS_SAXON, "saxonche not installed")
class SameBytesToctouTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["EN16931_ACTION_ROOT"] = str(ROOT)

    def test_old_design_consumes_replacement_fix_transforms_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary = base / "canary.txt"
            canary.write_bytes((CANARY + "\n").encode("ascii"))
            uri = canary.resolve().as_uri()
            hostile = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{uri}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            mark = "BENIGN_SNAPSHOT_MARK"
            benign = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f"<foo>{mark}</foo>\n".encode("ascii")
            )
            xml_path = base / "invoice.xml"
            xml_path.write_bytes(benign)
            xslt_path = dump_xsl(base)

            # Old design FAIL proof: last gate on original path, then replace.
            self.assertIn("ANTI-PATTERN", anti_pattern_transform_user_path.__doc__ or "")
            current_out = anti_pattern_transform_user_path(
                xml_path,
                xslt_path,
                after_gate=lambda p: p.write_bytes(hostile),
            )
            if sys.platform.startswith("linux"):
                self.assertIn(CANARY, current_out)
            self.assertNotIn(mark, current_out)

            # Restore canary + benign user file, then FIX path.
            canary.write_bytes((CANARY + "\n").encode("ascii"))
            xml_path.write_bytes(benign)
            data = xml_path.read_bytes()
            validate.gate_invoice_bytes(data, name=xml_path.name)
            xml_path.write_bytes(hostile)
            engine = validate.SaxonEngine()
            try:
                new_out = engine.transform_snapshot(
                    data, xslt_path, name=xml_path.name
                )
            finally:
                engine.close()
            self.assertIn(mark, new_out)
            self.assertNotIn(CANARY, new_out)
            self.assertEqual(canary.read_bytes(), (CANARY + "\n").encode("ascii"))
            self.assertEqual(xml_path.read_bytes(), hostile)

    def test_validate_files_hook_after_gate_never_transforms_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary = base / "canary.txt"
            canary.write_bytes((CANARY + "\n").encode("ascii"))
            uri = canary.resolve().as_uri()
            hostile = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{uri}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            benign = (ROOT / "testdata" / "official" / "CII_example3.xml").read_bytes()
            self.assertNotIn(b"<!DOCTYPE", benign)
            xml_path = base / "invoice.xml"
            xml_path.write_bytes(benign)
            before = sha256_bytes(benign)

            original = validate.refuse_invoice_dtd

            def refuse_then_replace(path: Path, data: bytes | None = None) -> bytes:
                snapshot = original(path, data)
                path.write_bytes(hostile)
                return snapshot

            validate.refuse_invoice_dtd = refuse_then_replace  # type: ignore[method-assign]
            snap_paths: list[Path] = []
            real_snap = validate.private_snapshot_file

            @contextlib.contextmanager
            def recording_snapshot(data: bytes):
                with real_snap(data) as snap:
                    snap_paths.append(snap.resolve())
                    self.assertEqual(snap.read_bytes(), benign)
                    self.assertEqual(xml_path.read_bytes(), hostile)
                    yield snap

            validate.private_snapshot_file = recording_snapshot  # type: ignore[method-assign]
            try:
                result = validate.validate_files(
                    [xml_path],
                    syntax="auto",
                    fail_on_raw="failed-assert",
                    version="1.3.16",
                    root=ROOT,
                    cwd=base,
                )
            finally:
                validate.refuse_invoice_dtd = original
                validate.private_snapshot_file = real_snap

            row = result["payload"]["files"][0]
            self.assertEqual(row["sha256"], before)
            self.assertEqual(row["verdict"], "pass")
            self.assertEqual(canary.read_bytes(), (CANARY + "\n").encode("ascii"))
            self.assertEqual(xml_path.read_bytes(), hostile)
            self.assertTrue(snap_paths)
            for snap in snap_paths:
                self.assertNotEqual(snap, xml_path.resolve())
                self.assertFalse(snap.exists())

    def test_validate_files_hook_http_zero_get(self) -> None:
        with http_probe_server() as (httpd, hits):
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/entity"
            hostile = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                + f'<!DOCTYPE foo [ <!ENTITY ext SYSTEM "{url}"> ]>\n'.encode("ascii")
                + b"<foo>&ext;</foo>\n"
            )
            benign = (ROOT / "testdata" / "official" / "CII_example3.xml").read_bytes()
            with tempfile.TemporaryDirectory() as tmp:
                xml_path = Path(tmp) / "invoice.xml"
                xml_path.write_bytes(benign)
                original = validate.refuse_invoice_dtd

                def refuse_then_replace(path: Path, data: bytes | None = None) -> bytes:
                    snapshot = original(path, data)
                    path.write_bytes(hostile)
                    return snapshot

                validate.refuse_invoice_dtd = refuse_then_replace  # type: ignore[method-assign]
                try:
                    result = validate.validate_files(
                        [xml_path],
                        syntax="auto",
                        fail_on_raw="failed-assert",
                        version="1.3.16",
                        root=ROOT,
                    )
                finally:
                    validate.refuse_invoice_dtd = original
                self.assertEqual(hits, [])
                self.assertEqual(
                    result["payload"]["files"][0]["sha256"], sha256_bytes(benign)
                )
                self.assertEqual(result["payload"]["files"][0]["verdict"], "pass")


@unittest.skipUnless(HAS_SAXON, "saxonche not installed")
class SnapshotCleanupValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["EN16931_ACTION_ROOT"] = str(ROOT)

    def test_unlink_always_fails_does_not_return_success(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example3.xml"
        secret = path.read_bytes()
        self.assertNotIn(b"<!DOCTYPE", secret)
        calls: list[str] = []

        def boom(path_str: str) -> None:
            calls.append(path_str)
            raise OSError("simulated unlink failure")

        original = validate.private_snapshot_file

        @contextlib.contextmanager
        def wrapped(data: bytes, **kwargs):
            kwargs.setdefault("attempts", 3)
            kwargs.setdefault("delay", 0.0)
            kwargs.setdefault("sleeper", lambda _s: None)
            kwargs["unlinker"] = boom
            with original(data, **kwargs) as snap:
                yield snap

        validate.private_snapshot_file = wrapped  # type: ignore[method-assign]
        try:
            with self.assertRaises(validate.SnapshotCleanupError) as ctx:
                validate.validate_files(
                    [path],
                    syntax="auto",
                    fail_on_raw="failed-assert",
                    version="1.3.16",
                    root=ROOT,
                    cwd=ROOT,
                )
            self.assertGreaterEqual(len(calls), 3)
            msg = str(ctx.exception)
            for item in calls:
                self.assertNotIn(item, msg)
            self.assertNotIn("ve-snap-", msg)
            snippet = secret[:40].decode("utf-8", "replace")
            self.assertNotIn(snippet, msg)
        finally:
            validate.private_snapshot_file = original
            for item in calls:
                try:
                    os.unlink(item)
                except OSError:
                    pass
                try:
                    os.rmdir(str(Path(item).parent))
                except OSError:
                    pass

    def test_unlink_retry_then_validation_completes(self) -> None:
        path = ROOT / "testdata" / "official" / "CII_example3.xml"
        real_unlink = os.unlink
        state = {"n": 0}

        def flaky(path_str: str) -> None:
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("first unlink fails")
            real_unlink(path_str)

        original = validate.private_snapshot_file

        @contextlib.contextmanager
        def wrapped(data: bytes, **kwargs):
            kwargs.setdefault("attempts", 4)
            kwargs.setdefault("delay", 0.0)
            kwargs.setdefault("sleeper", lambda _s: None)
            kwargs["unlinker"] = flaky
            with original(data, **kwargs) as snap:
                yield snap

        validate.private_snapshot_file = wrapped  # type: ignore[method-assign]
        try:
            result = validate.validate_files(
                [path],
                syntax="auto",
                fail_on_raw="failed-assert",
                version="1.3.16",
                root=ROOT,
                cwd=ROOT,
            )
        finally:
            validate.private_snapshot_file = original
        self.assertGreaterEqual(state["n"], 2)
        self.assertEqual(result["payload"]["verdict"], "pass")
        self.assertEqual(
            result["payload"]["files"][0]["sha256"], sha256_bytes(path.read_bytes())
        )


def _document_http_subprocess(
    xml_path: Path,
    xslt_path: Path,
    url: str,
    *,
    allowed_protocols: str | None,
    timeout: float = 12.0,
) -> subprocess.CompletedProcess[str]:
    """Run document() against url. allowed_protocols=None is the B mutant."""
    script = (
        "from saxonche import PySaxonProcessor\n"
        "import sys\n"
        "xml_path, xslt_path, url, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
        "proc = PySaxonProcessor(license=False)\n"
        "if mode == 'file':\n"
        "    proc.set_configuration_property(\n"
        "        'http://saxon.sf.net/feature/allowedProtocols', 'file'\n"
        "    )\n"
        "xslt = proc.new_xslt30_processor()\n"
        "exe = xslt.compile_stylesheet(stylesheet_file=xslt_path)\n"
        "exe.set_parameter('uri', proc.make_string_value(url))\n"
        "try:\n"
        "    out = exe.transform_to_string(source_file=xml_path)\n"
        "    sys.stdout.write('TRANSFORM_OK\\n')\n"
        "except Exception:\n"
        "    sys.stdout.write('TRANSFORM_FAIL\\n')\n"
    )
    mode = "file" if allowed_protocols == "file" else "none"
    return subprocess.run(
        [sys.executable, "-c", script, str(xml_path), str(xslt_path), url, mode],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@unittest.skipUnless(HAS_SAXON, "saxonche not installed")
class AllowedProtocolsBTests(unittest.TestCase):
    def test_document_http_mutant_gets_implementation_zero_get_fails_closed(self) -> None:
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<foo>NO_DTD_DOCUMENT_PROBE</foo>\n"
        )
        self.assertNotIn(b"<!DOCTYPE", xml)
        self.assertNotIn(b"DOCTYPE", xml)
        with http_probe_server() as (httpd, hits):
            host, port = httpd.server_address[:2]
            url = f"http://{host}:{port}/document-fn"
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                xml_path = base / "no-dtd.xml"
                xml_path.write_bytes(xml)
                xslt_path = document_http_xsl(base)
                hits.clear()
                try:
                    mutant = _document_http_subprocess(
                        xml_path, xslt_path, url, allowed_protocols=None
                    )
                except subprocess.TimeoutExpired:
                    mutant = None
                self.assertGreaterEqual(
                    len(hits),
                    1,
                    msg="B mutant without allowedProtocols=file must attempt HTTP",
                )
                hits.clear()
                impl = _document_http_subprocess(
                    xml_path, xslt_path, url, allowed_protocols="file"
                )
                self.assertEqual(hits, [])
                self.assertTrue(
                    impl.stdout.startswith("TRANSFORM_FAIL"),
                    msg="B with allowedProtocols=file must fail closed",
                )
                self.assertNotEqual(validate.SAXON_ALLOWED_PROTOCOLS_VALUE, "")
                if mutant is not None:
                    self.assertIn(mutant.stdout.split("\n", 1)[0], {"TRANSFORM_OK", "TRANSFORM_FAIL"})


class DetectSyntaxSnapshotTests(unittest.TestCase):
    def test_detect_syntax_uses_passed_bytes_not_path(self) -> None:
        cii = (ROOT / "testdata" / "official" / "CII_example1.xml").read_bytes()
        ubl = (ROOT / "testdata" / "official" / "ubl-tc434-creditnote1.xml").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.xml"
            path.write_bytes(ubl)
            self.assertEqual(validate.detect_syntax(path, data=cii), "CII")
            self.assertEqual(validate.detect_syntax(path), "UBL")

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

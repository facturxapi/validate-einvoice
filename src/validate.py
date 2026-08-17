#!/usr/bin/env python3
"""Validate EN16931 CII/UBL invoices with the official 1.3.16 XSLT.

Engine: SaxonC-HE 13.0 (saxonche==13.0.0).
Never logs invoice bytes. Never hands the original invoice path to Saxon.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SUPPORTED_VERSION = "1.3.16"
ENGINE_NAME = "SaxonC-HE 13.0"
ENGINE_PKG_PIN = "saxonche==13.0.0"
ENGINE_PKG_PREFIX = "13.0"

CII_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
UBL_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CREDIT_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"

CII_XSLT_REL = "vendor/en16931-1.3.16/xslt/EN16931-CII-validation.xslt"
UBL_XSLT_REL = "vendor/en16931-1.3.16/xslt/EN16931-UBL-validation.xslt"
CII_XSLT_SHA256 = "0b234dea2bbfee739b7761e607a992c17fab88773014ef56355b6158cfb1cc53"
UBL_XSLT_SHA256 = "39f9d282867f1a49e7708d9e29a53da89643e1ee56f10cec1ebcf1277595fcbd"

FAILED_BLOCK_RE = re.compile(
    r"<svrl:failed-assert\b([^>]*)>(.*?)</svrl:failed-assert>",
    re.DOTALL,
)
ID_ATTR_RE = re.compile(r'\bid="([^"]+)"')
LOCATION_ATTR_RE = re.compile(r'\blocation="([^"]*)"')
TEXT_RE = re.compile(r"<svrl:text\b[^>]*>(.*?)</svrl:text>", re.DOTALL)

XSLT_NS = "http://www.w3.org/1999/XSL/Transform"
SVRL_NS = "http://purl.oclc.org/dsdl/svrl"
# Call `name(` or function-ref `name#N`, optional NCName: / Q{uri} prefix.
# Longest names first. Not applied to svrl:text prose.
_XSLT_FORBIDDEN_FNS = (
    "unparsed-text-available",
    "unparsed-text-lines",
    "unparsed-text",
    "available-environment-variables",
    "environment-variable",
    "load-xquery-module",
    "function-lookup",
    "uri-collection",
    "collection",
    "doc-available",
    "json-doc",
    "document",
    "transform",
    "trace",
    "error",
    "doc",
)
XSLT_URI_FN_RE = re.compile(
    r"(?<![A-Za-z0-9._-])(?:[A-Za-z_][\w.-]*:|Q\{[^}]*\})?(?:"
    + "|".join(_XSLT_FORBIDDEN_FNS)
    + r")(?![A-Za-z0-9._-])(?:\s*\(|\s*#\s*\d+)",
    re.IGNORECASE,
)
XSLT_IO_ELEMENTS = {
    "include",
    "import",
    "import-schema",
    "source-document",
    "result-document",
    "evaluate",
    "use-package",
    "message",
    "assert",
}
LINUX_ONLY_MESSAGE = "this Action supports Linux runners only (ubuntu-latest)"
ANNOTATION_TEXT_LIMIT = 200

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2


class ConfigError(Exception):
    """Usage, input, or environment error (exit 2)."""


class EngineError(Exception):
    """XSLT engine error (exit 2)."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def action_root() -> Path:
    env = os.environ.get("EN16931_ACTION_ROOT")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent


def require_linux() -> None:
    """Fail-closed. No environment variable can skip this check."""
    if sys.platform != "linux":
        raise ConfigError(LINUX_ONLY_MESSAGE)


def split_patterns(raw_values: list[str]) -> list[str]:
    """Newline-delimited only. Spaces and commas stay in the pattern, including edges."""
    pieces: list[str] = []
    for raw in raw_values:
        for part in raw.split("\n"):
            if part.endswith("\r"):
                part = part[:-1]
            if part == "":
                continue
            pieces.append(part)
    return pieces


def expand_files(patterns: list[str], cwd: Path | None = None) -> list[Path]:
    """Expand globs. Fail closed if nothing matches."""
    base = cwd if cwd is not None else Path.cwd()
    found: list[Path] = []
    seen: set[str] = set()
    missing_patterns: list[str] = []

    for pattern in split_patterns(patterns):
        search = pattern
        path_obj = Path(pattern)
        if not path_obj.is_absolute():
            search = str(base / pattern)
        matches = sorted(glob.glob(search, recursive=True))
        if not matches and path_obj.is_file():
            matches = [str(path_obj)]
        if not matches:
            literal = (base / pattern) if not path_obj.is_absolute() else path_obj
            if literal.is_file():
                matches = [str(literal)]
        if not matches:
            missing_patterns.append(pattern)
            continue
        for match in matches:
            candidate = Path(match)
            if not candidate.is_file():
                continue
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)

    if missing_patterns and not found:
        raise ConfigError("no XML files matched: " + ", ".join(missing_patterns))
    if missing_patterns:
        raise ConfigError("pattern matched nothing: " + ", ".join(missing_patterns))
    if not found:
        raise ConfigError("no XML files matched the files input")
    return found


def display_path(path: Path, cwd: Path | None = None) -> str:
    """Relative posix path when possible; basename otherwise."""
    here = cwd if cwd is not None else Path.cwd()
    try:
        return path.resolve().relative_to(here.resolve()).as_posix()
    except ValueError:
        return path.name


def hardened_parse(path: Path) -> ET.ElementTree:
    """Single untrusted-XML parse. DTD, entities, and external refs are refused."""
    from defusedxml.ElementTree import parse as defused_parse

    try:
        return defused_parse(
            str(path),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise ConfigError(f"XML rejected by hardened parser ({path.name}): {exc}") from exc


def serialize_safe_xml(tree: ET.ElementTree) -> str:
    """Re-serialize without DOCTYPE or external entities — Saxon's only input."""
    payload = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True, method="xml")
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return payload


def detect_syntax_from_root(root: ET.Element) -> str:
    root_tag = root.tag
    if root_tag.startswith("{"):
        ns, local = root_tag[1:].split("}", 1)
    else:
        ns, local = "", root_tag
    if ns == CII_NS or local == "CrossIndustryInvoice":
        return "CII"
    if ns in {UBL_INVOICE_NS, UBL_CREDIT_NS} or local in {"Invoice", "CreditNote"}:
        return "UBL"
    raise ConfigError("cannot detect EN16931 syntax from document element")


def detect_syntax(path: Path) -> str:
    tree = hardened_parse(path)
    return detect_syntax_from_root(tree.getroot())


def resolve_syntax(path: Path, requested: str) -> str:
    detected = detect_syntax(path)
    mode = requested.lower()
    if mode == "auto":
        return detected
    if mode == "cii":
        forced = "CII"
    elif mode == "ubl":
        forced = "UBL"
    else:
        raise ConfigError("syntax must be auto, cii, or ubl")
    if detected != forced:
        raise ConfigError(f"{path.name}: syntax={mode} but document is {detected}")
    return forced


def parse_fail_on(raw: str) -> dict[str, Any]:
    value = (raw or "failed-assert").strip()
    lowered = value.lower()
    if lowered in {"failed-assert", "any", "all"}:
        return {"mode": "failed-assert", "ids": []}
    if lowered in {"never", "none"}:
        return {"mode": "never", "ids": []}
    ids = [item.strip() for item in value.split(",") if item.strip()]
    if not ids:
        raise ConfigError("fail-on is empty")
    return {"mode": "ids", "ids": ids}


def parse_failed_asserts(svrl: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in FAILED_BLOCK_RE.finditer(svrl):
        attrs, body = match.group(1), match.group(2)
        id_match = ID_ATTR_RE.search(attrs)
        loc_match = LOCATION_ATTR_RE.search(attrs)
        text_match = TEXT_RE.search(body)
        text = ""
        if text_match:
            text = re.sub(r"\s+", " ", text_match.group(1)).strip()
        rows.append(
            {
                "id": id_match.group(1) if id_match else "?",
                "location": loc_match.group(1) if loc_match else "",
                "text": text,
            }
        )
    return rows


def require_engine_pkg() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("importlib.metadata is required") from exc
    try:
        found = version("saxonche")
    except PackageNotFoundError as exc:
        raise ConfigError(
            "saxonche is not installed. "
            f"Install {ENGINE_PKG_PIN} (see requirements.txt)."
        ) from exc
    if not found.startswith(ENGINE_PKG_PREFIX):
        raise ConfigError(f"{ENGINE_PKG_PIN} is required, found saxonche=={found}")
    return found


def vendor_root(root: Path) -> Path:
    return (root / "vendor").resolve()


def assert_xslt_under_vendor(path: Path, root: Path) -> None:
    resolved = path.resolve()
    vroot = vendor_root(root)
    if not resolved.is_relative_to(vroot):
        raise ConfigError(f"XSLT escaped vendor root: {path.name}")


def _tag_parts(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, local
    return "", tag


def xpath_has_forbidden_fn(expr: str) -> bool:
    """True if expr calls or references a forbidden function (incl. lookup)."""
    return XSLT_URI_FN_RE.search(expr) is not None


def assert_xslt_has_no_external_deps(path: Path) -> None:
    """Refuse include/import by XSLT namespace and URI-access functions in XPath."""
    tree = hardened_parse(path)
    for elem in tree.getroot().iter():
        ns, local = _tag_parts(elem.tag)
        if ns == XSLT_NS and local in XSLT_IO_ELEMENTS:
            raise EngineError(f"XSLT has forbidden include/URI feature: {path.name}")
        if ns == SVRL_NS and local == "text":
            continue
        for _attr_name, attr_val in elem.attrib.items():
            if xpath_has_forbidden_fn(attr_val):
                raise EngineError(f"XSLT has forbidden include/URI feature: {path.name}")
        if ns != SVRL_NS and elem.text and xpath_has_forbidden_fn(elem.text):
            raise EngineError(f"XSLT has forbidden include/URI feature: {path.name}")
        if elem.tail and xpath_has_forbidden_fn(elem.tail):
            raise EngineError(f"XSLT has forbidden include/URI feature: {path.name}")


def load_xslt(root: Path, version: str) -> dict[str, dict[str, str | Path]]:
    if version != SUPPORTED_VERSION:
        raise ConfigError(
            f"version {version} is not vendored; only {SUPPORTED_VERSION} is available"
        )
    mapping = {
        "CII": (root / CII_XSLT_REL, CII_XSLT_SHA256, CII_XSLT_REL),
        "UBL": (root / UBL_XSLT_REL, UBL_XSLT_SHA256, UBL_XSLT_REL),
    }
    resolved: dict[str, dict[str, str | Path]] = {}
    for syntax, (path, expected, logical) in mapping.items():
        if not path.is_file():
            raise ConfigError(f"vendored XSLT missing: {logical}")
        assert_xslt_under_vendor(path, root)
        digest = sha256_file(path)
        if digest != expected:
            raise ConfigError(
                f"vendored XSLT SHA256 mismatch for {logical}: "
                f"expected {expected}, got {digest}"
            )
        assert_xslt_has_no_external_deps(path)
        resolved[syntax] = {"path": path, "sha256": digest, "logical": logical}
    return resolved


class SaxonEngine:
    def __init__(self) -> None:
        require_engine_pkg()
        try:
            from saxonche import PySaxonProcessor
        except ImportError as exc:
            raise ConfigError(
                "saxonche cannot be imported. "
                f"Install {ENGINE_PKG_PIN}."
            ) from exc
        self._proc = PySaxonProcessor(license=False)
        self._xslt = self._proc.new_xslt30_processor()
        self._compiled: dict[str, Any] = {}

    def transform(self, xml_text: str, xslt_path: Path) -> str:
        """Transform re-serialized XML text. Never accepts an invoice filesystem path."""
        key = str(xslt_path)
        executable = self._compiled.get(key)
        if executable is None:
            executable = self._xslt.compile_stylesheet(stylesheet_file=key)
            if executable is None:
                raise EngineError(f"XSLT compile failed: {xslt_path.name}")
            self._compiled[key] = executable
        node = self._proc.parse_xml(xml_text=xml_text)
        if node is None:
            raise EngineError("Saxon could not parse the hardened XML")
        svrl = executable.transform_to_string(xdm_node=node)
        if svrl is None:
            raise EngineError("XSLT produced no SVRL")
        return svrl

    def close(self) -> None:
        closer = getattr(self._proc, "release", None)
        if callable(closer):
            closer()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def file_triggers_fail(row: dict[str, Any], fail_on: dict[str, Any]) -> bool:
    if fail_on["mode"] == "never":
        return False
    ids = list(row["failed_assert_ids"])
    if fail_on["mode"] == "failed-assert":
        return len(ids) > 0
    watched = set(fail_on["ids"])
    return any(item in watched for item in ids)


def _truncate_before_encode(text: str, limit: int = ANNOTATION_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def encode_workflow_prop(value: str) -> str:
    text = _truncate_before_encode(value)
    return (
        text.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def encode_workflow_msg(value: str) -> str:
    text = _truncate_before_encode(value)
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation_line(rel_path: str, item: dict[str, str]) -> str:
    rule_id = item["id"]
    text = item["text"] or "failed-assert"
    file_prop = encode_workflow_prop(rel_path)
    title_prop = encode_workflow_prop(rule_id)
    message = encode_workflow_msg(f"{rule_id}: {text}")
    return f"::error file={file_prop},title={title_prop}::{message}"


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "## EN16931 1.3.16 validation",
        "",
        f"- Engine: `{report['engine']}` (`{report['engine_pkg']}`)",
        f"- XSLT version: `{report['version']}`",
        f"- Syntax: `{report['syntax']}`",
        f"- fail-on: `{report['fail_on']}`",
        f"- Verdict: **{report['verdict']}**",
        f"- Files with failed-assert: {report['failed_count']} / {len(report['files'])}",
        f"- Report SHA256: `{report['report_sha256']}`",
        "",
        "| File | Syntax | Verdict | failed-assert | ids |",
        "|---|---|---|---:|---|",
    ]
    for row in report["files"]:
        ids = ",".join(row["failed_assert_ids"]) if row["failed_assert_ids"] else ""
        lines.append(
            f"| `{row['path']}` | {row['syntax']} | {row['verdict']} | "
            f"{row['failed_assert_count']} | {ids} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_github_output(values: dict[str, str], report_text: str) -> None:
    dest = os.environ.get("GITHUB_OUTPUT")
    if not dest:
        return
    delimiter = "EN16931_REPORT_EOF"
    if delimiter in report_text:
        raise EngineError("report contains output delimiter")
    with open(dest, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
        handle.write(f"report<<{delimiter}\n")
        handle.write(report_text)
        if not report_text.endswith("\n"):
            handle.write("\n")
        handle.write(f"{delimiter}\n")


def write_step_summary(text: str) -> None:
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def validate_files(
    files: list[Path],
    *,
    syntax: str,
    fail_on_raw: str,
    version: str,
    root: Path,
    cwd: Path | None = None,
    emit_annotations: bool = False,
) -> dict[str, Any]:
    fail_on = parse_fail_on(fail_on_raw)
    xslt = load_xslt(root, version)
    engine = SaxonEngine()
    rows: list[dict[str, Any]] = []
    annotations: list[str] = []
    try:
        for path in files:
            tree = hardened_parse(path)
            detected = detect_syntax_from_root(tree.getroot())
            mode = syntax.lower()
            if mode == "auto":
                resolved_syntax = detected
            elif mode == "cii":
                resolved_syntax = "CII"
                if detected != resolved_syntax:
                    raise ConfigError(f"{path.name}: syntax={mode} but document is {detected}")
            elif mode == "ubl":
                resolved_syntax = "UBL"
                if detected != resolved_syntax:
                    raise ConfigError(f"{path.name}: syntax={mode} but document is {detected}")
            else:
                raise ConfigError("syntax must be auto, cii, or ubl")
            safe_xml = serialize_safe_xml(tree)
            xslt_info = xslt[resolved_syntax]
            svrl = engine.transform(safe_xml, Path(xslt_info["path"]))
            failed = parse_failed_asserts(svrl)
            ids = [item["id"] for item in failed]
            rel = display_path(path, cwd=cwd)
            row = {
                "failed_assert_count": len(failed),
                "failed_assert_ids": ids,
                "path": rel,
                "sha256": sha256_file(path),
                "syntax": resolved_syntax,
                "verdict": "fail" if ids else "pass",
                "xslt": xslt_info["logical"],
                "xslt_sha256": xslt_info["sha256"],
            }
            rows.append(row)
            if emit_annotations:
                for item in failed:
                    annotations.append(annotation_line(rel, item))
    finally:
        engine.close()

    hashed_files = []
    for row in rows:
        hashed_files.append(
            {
                "failed_assert_count": row["failed_assert_count"],
                "failed_assert_ids": list(row["failed_assert_ids"]),
                "path": row["path"],
                "sha256": row["sha256"],
                "syntax": row["syntax"],
                "verdict": row["verdict"],
                "xslt": row["xslt"],
                "xslt_sha256": row["xslt_sha256"],
            }
        )
    hashed_files.sort(key=lambda item: item["path"])

    failing = [row for row in hashed_files if file_triggers_fail(row, fail_on)]
    verdict = "fail" if failing else "pass"
    payload = {
        "engine": ENGINE_NAME,
        "engine_pkg": ENGINE_PKG_PIN,
        "fail_on": fail_on_raw.strip() or "failed-assert",
        "failed_count": sum(1 for row in hashed_files if row["failed_assert_count"] > 0),
        "files": hashed_files,
        "syntax": syntax,
        "verdict": verdict,
        "version": version,
    }
    report_text = canonical_json(payload)
    report_sha = sha256_text(report_text)
    payload["report_sha256"] = report_sha
    return {
        "payload": payload,
        "report_text": report_text,
        "report_sha256": report_sha,
        "annotations": annotations,
        "exit_code": EXIT_FAIL if verdict == "fail" else EXIT_OK,
    }


def print_console(result: dict[str, Any]) -> None:
    payload = result["payload"]
    print(f"engine : {payload['engine']} ({payload['engine_pkg']})")
    print(f"version: {payload['version']}")
    print(f"syntax : {payload['syntax']}")
    print(f"fail-on: {payload['fail_on']}")
    print()
    print(f"{'file':<42} {'syn':<4} {'n':>3}  verdict  ids")
    print("-" * 88)
    for row in payload["files"]:
        ids = ",".join(row["failed_assert_ids"])
        print(
            f"{row['path']:<42} {row['syntax']:<4} {row['failed_assert_count']:>3}  "
            f"{row['verdict']:<6}  {ids}"
        )
    print("-" * 88)
    print(f"verdict      : {payload['verdict']}")
    print(f"failed-count : {payload['failed_count']}")
    print(f"report-sha256: {result['report_sha256']}")
    for line in result["annotations"]:
        print(line)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate EN16931 CII/UBL XML with official XSLT 1.3.16."
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Newline-delimited glob(s) of XML invoices. Overrides INPUT_FILES when set.",
    )
    parser.add_argument(
        "--syntax",
        default=None,
        help="auto | cii | ubl (default: auto, or INPUT_SYNTAX).",
    )
    parser.add_argument(
        "--fail-on",
        dest="fail_on",
        default=None,
        help="failed-assert | never | comma-separated ids.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help=f"XSLT version (only {SUPPORTED_VERSION} is vendored).",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Write the canonical JSON report to this path.",
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Read INPUT_* env, emit annotations, GITHUB_OUTPUT, step summary.",
    )
    return parser.parse_args(argv)


def resolve_inputs(args: argparse.Namespace) -> dict[str, Any]:
    files = args.files
    if not files:
        env_files = os.environ.get("INPUT_FILES", "")
        files = [env_files] if env_files.strip() else []
    if not files or files == [""]:
        raise ConfigError("files input is required")
    syntax = args.syntax or os.environ.get("INPUT_SYNTAX") or "auto"
    fail_on = args.fail_on or os.environ.get("INPUT_FAIL_ON") or "failed-assert"
    version = args.version or os.environ.get("INPUT_VERSION") or SUPPORTED_VERSION
    return {
        "files": files,
        "syntax": syntax.strip(),
        "fail_on": fail_on.strip(),
        "version": version.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        require_linux()
        inputs = resolve_inputs(args)
        root = action_root()
        xml_files = expand_files(inputs["files"])
        emit = bool(args.github or os.environ.get("GITHUB_ACTIONS") == "true")
        result = validate_files(
            xml_files,
            syntax=inputs["syntax"],
            fail_on_raw=inputs["fail_on"],
            version=inputs["version"],
            root=root,
            emit_annotations=emit,
        )
    except (ConfigError, EngineError) as exc:
        github = os.environ.get("GITHUB_ACTIONS") == "true"
        message = str(exc)
        if github:
            print(f"::error::{encode_workflow_msg(message)}", file=sys.stderr)
        else:
            print(f"error: {message}", file=sys.stderr)
        return EXIT_CONFIG

    report_path = args.report
    if report_path is None and emit:
        report_path = "en16931-report.json"
    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result["report_text"], encoding="utf-8")
        result["report_path"] = display_path(out)
    else:
        result["report_path"] = ""

    print_console(result)
    if emit:
        write_github_output(
            {
                "verdict": result["payload"]["verdict"],
                "failed-count": str(result["payload"]["failed_count"]),
                "report-sha256": result["report_sha256"],
                "report-path": result["report_path"],
            },
            result["report_text"],
        )
        write_step_summary(markdown_summary({**result["payload"], "report_sha256": result["report_sha256"]}))
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate EN16931 CII/UBL invoices with the official 1.3.16 XSLT.

Engine: SaxonC-HE 13.0 (saxonche==13.0.0).
Never logs invoice bytes.
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
FIRED_RE = re.compile(r"<svrl:fired-rule\b")

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


def split_patterns(raw_values: list[str]) -> list[str]:
    pieces: list[str] = []
    for raw in raw_values:
        for part in re.split(r"[\n,]+", raw):
            part = part.strip()
            if part:
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
        raise ConfigError(
            "no XML files matched: " + ", ".join(missing_patterns)
        )
    if missing_patterns:
        raise ConfigError(
            "pattern matched nothing: " + ", ".join(missing_patterns)
        )
    if not found:
        raise ConfigError("no XML files matched the files input")
    return found


def display_path(path: Path, cwd: Path | None = None) -> str:
    """Relative posix path when possible; basename otherwise. Never force a home path."""
    here = cwd if cwd is not None else Path.cwd()
    try:
        return path.resolve().relative_to(here.resolve()).as_posix()
    except ValueError:
        return path.name


def detect_syntax(path: Path) -> str:
    root_tag = None
    try:
        for _event, elem in ET.iterparse(path, events=("start",)):
            root_tag = elem.tag
            break
    except ET.ParseError as exc:
        raise ConfigError(f"XML is not well-formed ({path.name}): {exc}") from exc
    if root_tag is None:
        raise ConfigError(f"XML has no document element ({path.name})")
    if root_tag.startswith("{"):
        ns, local = root_tag[1:].split("}", 1)
    else:
        ns, local = "", root_tag
    if ns == CII_NS or local == "CrossIndustryInvoice":
        return "CII"
    if ns in {UBL_INVOICE_NS, UBL_CREDIT_NS} or local in {"Invoice", "CreditNote"}:
        return "UBL"
    raise ConfigError(
        f"cannot detect EN16931 syntax from document element ({path.name})"
    )


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
        raise ConfigError(
            f"{path.name}: syntax={mode} but document is {detected}"
        )
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
        raise ConfigError(
            f"{ENGINE_PKG_PIN} is required, found saxonche=={found}"
        )
    return found


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
        digest = sha256_file(path)
        if digest != expected:
            raise ConfigError(
                f"vendored XSLT SHA256 mismatch for {logical}: "
                f"expected {expected}, got {digest}"
            )
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

    def transform(self, xml_path: Path, xslt_path: Path) -> str:
        key = str(xslt_path)
        executable = self._compiled.get(key)
        if executable is None:
            executable = self._xslt.compile_stylesheet(stylesheet_file=key)
            if executable is None:
                raise EngineError(f"XSLT compile failed: {xslt_path.name}")
            self._compiled[key] = executable
        svrl = executable.transform_to_string(source_file=str(xml_path))
        if svrl is None:
            raise EngineError(f"XSLT produced no SVRL: {xml_path.name}")
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


ANNOTATION_TEXT_MAX = 220


def escape_workflow_data(value: str) -> str:
    """B. User text after the second ``::`` (toolkit ``escapeData``).

    ``%`` → ``%25``, then ``\\r`` → ``%0D``, then ``\\n`` → ``%0A``.
    Does not encode ``:`` or ``,`` so rule IDs stay readable.
    """
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def escape_workflow_property(value: str) -> str:
    """A. Command properties ``file=`` / ``title=`` (toolkit ``escapeProperty``).

    Same as ``escape_workflow_data``, then ``:`` → ``%3A``, then ``,`` → ``%2C``.
    """
    return (
        escape_workflow_data(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def workflow_error_line(message: object) -> str:
    """Single-line ``::error::`` command for configuration / engine errors."""
    return f"::error::{escape_workflow_data(str(message))}"


def annotation_line(rel_path: str, item: dict[str, str]) -> str:
    # A: properties (file=, title=) encode : and ,
    file_path = escape_workflow_property(rel_path)
    title = escape_workflow_property(item["id"])
    # B: user-visible message encodes % / CR / LF only — rule IDs stay readable.
    rule_id = escape_workflow_data(item["id"])
    text = escape_workflow_data(item["text"] or "failed-assert")
    # Truncate AFTER escaping so a long ``%0A`` run cannot survive decode.
    if len(text) > ANNOTATION_TEXT_MAX:
        text = text[: ANNOTATION_TEXT_MAX - 3] + "..."
    return f"::error file={file_path},title={title}::{rule_id}: {text}"


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


def configure_stdio() -> None:
    """Force UTF-8 on stdout/stderr so SVRL text (e.g. Σ) cannot crash Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def emit_line(text: str, *, file: Any = None) -> None:
    """Print one line without raising UnicodeEncodeError on a narrow codec."""
    target = sys.stderr if file is sys.stderr else (file or sys.stdout)
    try:
        print(text, file=target)
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(target, "encoding", None) or "utf-8"
    payload = (text + "\n").encode(encoding, errors="replace")
    buffer = getattr(target, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
        return
    target.write(payload.decode(encoding, errors="replace"))


def write_github_output(values: dict[str, str], report_text: str) -> None:
    dest = os.environ.get("GITHUB_OUTPUT")
    if not dest:
        return
    delimiter = "EN16931_REPORT_EOF"
    if delimiter in report_text:
        raise EngineError("report contains output delimiter")
    # newline="\n": on Windows, text mode would otherwise write CR LF.
    # GitHub then exposes verdict=fail\r, and `!= "fail"` is true.
    with open(dest, "a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
        handle.write(f"report<<{delimiter}\n")
        handle.write(report_text.replace("\r\n", "\n").replace("\r", "\n"))
        if not report_text.endswith("\n"):
            handle.write("\n")
        handle.write(f"{delimiter}\n")


def write_step_summary(text: str) -> None:
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8", newline="\n") as handle:
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
            resolved_syntax = resolve_syntax(path, syntax)
            xslt_info = xslt[resolved_syntax]
            svrl = engine.transform(path, Path(xslt_info["path"]))
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
    # report_sha256 is a convenience on the in-memory object only.
    # The hashed document excludes it so two runs compare equal.
    return {
        "payload": payload,
        "report_text": report_text,
        "report_sha256": report_sha,
        "annotations": annotations,
        "exit_code": EXIT_FAIL if verdict == "fail" else EXIT_OK,
    }


def print_console(result: dict[str, Any]) -> None:
    payload = result["payload"]
    emit_line(f"engine : {payload['engine']} ({payload['engine_pkg']})")
    emit_line(f"version: {payload['version']}")
    emit_line(f"syntax : {payload['syntax']}")
    emit_line(f"fail-on: {payload['fail_on']}")
    emit_line("")
    emit_line(f"{'file':<42} {'syn':<4} {'n':>3}  verdict  ids")
    emit_line("-" * 88)
    for row in payload["files"]:
        ids = ",".join(row["failed_assert_ids"])
        emit_line(
            f"{row['path']:<42} {row['syntax']:<4} {row['failed_assert_count']:>3}  "
            f"{row['verdict']:<6}  {ids}"
        )
    emit_line("-" * 88)
    emit_line(f"verdict      : {payload['verdict']}")
    emit_line(f"failed-count : {payload['failed_count']}")
    emit_line(f"report-sha256: {result['report_sha256']}")
    for line in result["annotations"]:
        emit_line(line)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate EN16931 CII/UBL XML with official XSLT 1.3.16."
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Glob(s) of XML invoices. Overrides INPUT_FILES when set.",
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
        env_files = os.environ.get("INPUT_FILES", "").strip()
        files = [env_files] if env_files else []
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
        print(workflow_error_line(exc) if os.environ.get("GITHUB_ACTIONS") == "true" else f"error: {exc}", file=sys.stderr)
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

    # Outputs first: a later console encode error must not leave MUTANT_VERDICT empty.
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
    print_console(result)
    return int(result["exit_code"])


if __name__ == "__main__":
    configure_stdio()
    sys.exit(main())

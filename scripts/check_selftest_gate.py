#!/usr/bin/env python3
"""Prove Linux==macOS==Windows for selftest official and mutant reports."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_IDS = ROOT / "testdata" / "expected-ids.json"
REQUIRED_OS = ("ubuntu-latest", "macos-latest", "windows-latest")
REQUIRED_PY = "3.13"
REPORT_NAMES = ("official.json", "mutants.json", "testdata.sha256")


def fail(message: str) -> None:
    print(f"SELFTEST-GATE FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_digest(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        digest, name = line.split(None, 1)
        out[name] = digest
    return out


def collect_cells(root: Path) -> dict[tuple[str, str], Path]:
    found: dict[tuple[str, str], Path] = {}
    if not root.is_dir():
        fail(f"missing reports dir {root}")
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        name = path.name
        prefix = "selftest-reports-"
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix) :]
        if "-py" not in rest:
            fail(f"unexpected artifact dir {name}")
        os_name, py = rest.rsplit("-py", 1)
        found[(os_name, py)] = path
    return found


def load_cell(path: Path) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    for name in REPORT_NAMES:
        file_path = path / name
        if not file_path.is_file():
            fail(f"missing {path.name}/{name}")
        data = file_path.read_bytes()
        if b"\r" in data:
            fail(f"{path.name}/{name} contains CR")
        blobs[name] = data
    return blobs


def compare_reports(label: str, blobs: dict[str, bytes], expected_ids: dict[str, list[str]]) -> dict:
    try:
        payload = json.loads(blobs[label].decode("utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{label}: invalid JSON: {exc}")
    files = payload.get("files")
    if not isinstance(files, list):
        fail(f"{label}: missing files[]")
    if len(files) != 10:
        fail(f"{label}: expected 10 files, got {len(files)}")
    testdata = parse_digest(blobs["testdata.sha256"].decode("utf-8"))
    for row in files:
        path = row["path"]
        want = testdata.get(path)
        if want is None:
            fail(f"{label}: {path} missing from testdata.sha256")
        if row["sha256"] != want:
            fail(f"{label}: {path} files[].sha256 != testdata digest")
        if "\r" in path or "\\" in path:
            fail(f"{label}: non-posix path {path!r}")
    if label == "official.json":
        if payload["verdict"] != "pass" or payload["failed_count"] != 0:
            fail(f"{label}: expected clean pass")
        bad = [row["path"] for row in files if row["verdict"] != "pass" or row["failed_assert_ids"]]
        if bad:
            fail(f"{label}: non-pass files {bad}")
    else:
        if payload["verdict"] != "fail":
            fail(f"{label}: expected fail verdict")
        got = {Path(row["path"]).name: row["failed_assert_ids"] for row in files}
        if got != expected_ids:
            fail(f"{label}: failed_assert_ids mismatch")
    print(f"{label} report-sha256 {sha256_bytes(blobs[label])}")
    print(f"{label} files={len(files)} verdict={payload['verdict']} failed_count={payload['failed_count']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        fail("usage: check_selftest_gate.py <reports-dir>")
    root = Path(args[0])
    cells = collect_cells(root)
    if not cells:
        fail(f"no selftest-reports-* dirs under {root}")

    required = [(os_name, REQUIRED_PY) for os_name in REQUIRED_OS]
    missing = [f"{os_name}-py{py}" for os_name, py in required if (os_name, py) not in cells]
    if missing:
        fail("missing required artifact cells: " + ", ".join(missing))

    expected_ids = json.loads(EXPECTED_IDS.read_text(encoding="utf-8"))
    loaded: dict[tuple[str, str], dict[str, bytes]] = {}
    for key, path in sorted(cells.items()):
        loaded[key] = load_cell(path)

    reference = loaded[(REQUIRED_OS[0], REQUIRED_PY)]
    for label in REPORT_NAMES:
        print(f"reference {REQUIRED_OS[0]} py{REQUIRED_PY} {label} {sha256_bytes(reference[label])}")

    for key, blobs in loaded.items():
        os_name, py = key
        cell_name = f"selftest-reports-{os_name}-py{py}"
        for name in ("official.json", "mutants.json"):
            if blobs[name] != reference[name]:
                fail(
                    f"{cell_name}/{name} bytes != {REQUIRED_OS[0]}-py{REQUIRED_PY} "
                    f"({sha256_bytes(blobs[name])} vs {sha256_bytes(reference[name])})"
                )
        ref_td = parse_digest(reference["testdata.sha256"].decode("utf-8"))
        got_td = parse_digest(blobs["testdata.sha256"].decode("utf-8"))
        if got_td != ref_td:
            fail(f"{cell_name}/testdata.sha256 fixture hashes != {REQUIRED_OS[0]}-py{REQUIRED_PY}")

    compare_reports("official.json", reference, expected_ids)
    compare_reports("mutants.json", reference, expected_ids)

    testdata = parse_digest(reference["testdata.sha256"].decode("utf-8"))
    if len(testdata) != 20:
        fail(f"testdata.sha256 expected 20 entries, got {len(testdata)}")

    print(
        "SELFTEST-GATE PASS: Linux==macOS==Windows "
        f"(cells={len(cells)}, fixture files={len(testdata)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

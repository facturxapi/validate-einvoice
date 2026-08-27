#!/usr/bin/env python3
"""Supply-chain gate: pinned third-party actions and hashed Python dependencies."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS_FILE = ROOT / "supply-chain" / "action-pins.yaml"
REQUIREMENTS = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock"
SCAN_PATHS = (
    ROOT / ".github" / "workflows",
    ROOT / "action.yml",
    ROOT / "examples",
)

USES_LINE_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s+(?P<repo>[^@\s]+/[^@\s]+)@(?P<ref>[0-9a-f]{40})(?:\s+#\s*(?P<comment>.*))?\s*$",
    re.MULTILINE,
)
LOCK_ENTRY_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s\\]+)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pins() -> dict[str, dict[str, str]]:
    text = PINS_FILE.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:)", text)
    pins: dict[str, dict[str, str]] = {}
    for block in blocks:
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        header, _, body = block.partition(":")
        key = header.strip()
        repo_match = re.search(r"^\s+repository:\s+(\S+)", body, re.MULTILINE)
        ref_match = re.search(r"^\s+ref:\s+([0-9a-f]{40})", body, re.MULTILINE)
        version_match = re.search(r"^\s+version:\s+(\S+)", body, re.MULTILINE)
        if not ref_match:
            raise ValueError(f"{key}: missing ref in {PINS_FILE.name}")
        pins[key] = {
            "repository": repo_match.group(1) if repo_match else key,
            "ref": ref_match.group(1),
            "version": version_match.group(1) if version_match else "",
        }
    if not pins:
        raise ValueError(f"{PINS_FILE.name} contains no action pins")
    return pins


def iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCAN_PATHS:
        if entry.is_file():
            files.append(entry)
        elif entry.is_dir():
            files.extend(sorted(entry.glob("*.yml")))
            files.extend(sorted(entry.glob("*.yaml")))
    return files


def is_local_uses_line(line: str) -> bool:
    stripped = line.strip()
    if "uses:" not in stripped:
        return False
    after = stripped.split("uses:", 1)[1].strip()
    return after == "./" or after.startswith("./")


def check_external_uses(errors: list[str]) -> None:
    pins = load_pins()
    seen: dict[str, set[str]] = {key: set() for key in pins}

    for path in iter_scan_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for lineno, raw in enumerate(text.splitlines(), start=1):
            if "uses:" not in raw:
                continue
            if is_local_uses_line(raw):
                continue
            match = USES_LINE_RE.match(raw)
            if not match:
                after = raw.split("uses:", 1)[1].strip()
                errors.append(
                    f"{rel}:{lineno}: external uses must be owner/repo@<40-char-sha>, got {after!r}"
                )
                continue
            repo = match.group("repo")
            ref = match.group("ref")
            if repo not in pins:
                errors.append(
                    f"{rel}:{lineno}: {repo} is not listed in supply-chain/action-pins.yaml"
                )
                continue
            seen[repo].add(ref)
            want = pins[repo]["ref"]
            if ref != want:
                errors.append(
                    f"{rel}:{lineno}: {repo}@{ref} != manifest pin {want}"
                )
            version = pins[repo]["version"]
            comment = (match.group("comment") or "").strip()
            if version and version not in comment:
                errors.append(
                    f"{rel}:{lineno}: {repo} pin comment must include {version!r}"
                )

    for repo, refs in seen.items():
        if not refs:
            errors.append(f"missing manifest-pinned uses for {repo} in scanned workflows")


def parse_lock() -> tuple[str, str, list[str]]:
    text = LOCK.read_text(encoding="utf-8")
    hashes: list[str] = []
    name = version = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            hashes.append(line.removeprefix("--hash="))
            continue
        entry = LOCK_ENTRY_RE.match(line)
        if entry:
            name = entry.group("name")
            version = entry.group("version")
            continue
        if line.endswith("\\"):
            continue
        raise ValueError(f"unparseable lock line: {raw!r}")
    if not name or not version or not hashes:
        raise ValueError("requirements.lock must name one package with hashes")
    return name, version, hashes


def check_requirements_lock(errors: list[str]) -> None:
    req_line = REQUIREMENTS.read_text(encoding="utf-8").strip().splitlines()
    req_nonempty = [line.strip() for line in req_line if line.strip() and not line.startswith("#")]
    if len(req_nonempty) != 1:
        errors.append("requirements.txt must contain exactly one dependency line")
        return
    req_spec = req_nonempty[0]
    try:
        name, version, hashes = parse_lock()
    except ValueError as exc:
        errors.append(f"requirements.lock: {exc}")
        return
    lock_spec = f"{name}=={version}"
    if req_spec != lock_spec:
        errors.append(
            f"requirements.txt ({req_spec!r}) != requirements.lock ({lock_spec!r})"
        )
    if len(hashes) < 3:
        errors.append("requirements.lock must pin at least three wheel hashes")


def fingerprint_supply_chain_tree() -> dict[str, str]:
    paths = sorted(
        p
        for p in (
            PINS_FILE,
            REQUIREMENTS,
            LOCK,
            ROOT / "SECURITY.md",
            ROOT / "docs" / "RELEASE_AND_RULESET.md",
            ROOT / "scripts" / "check_supply_chain.py",
        )
        if p.is_file()
    )
    paths.extend(iter_scan_files())
    out: dict[str, str] = {}
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        out[rel] = sha256_file(path)
    return out


def check_tree_digest(errors: list[str]) -> None:
    digest_path = ROOT / "supply-chain" / "tree.sha256"
    if not digest_path.is_file():
        errors.append(f"missing {digest_path.relative_to(ROOT).as_posix()}")
        return
    lines = [
        line.strip()
        for line in digest_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    pinned = {}
    for line in lines:
        digest, name = line.split(None, 1)
        pinned[name] = digest
    current = fingerprint_supply_chain_tree()
    missing = sorted(set(pinned) - set(current))
    extra = sorted(set(current) - set(pinned))
    if missing:
        errors.append(f"tree digest missing files: {missing}")
    if extra:
        errors.append(f"tree digest has unexpected files: {extra}")
    for rel, digest in sorted(current.items()):
        want = pinned.get(rel)
        if want and want != digest:
            errors.append(f"tree digest drift: {rel}")


def main(argv: list[str] | None = None) -> int:
    del argv
    errors: list[str] = []
    if not PINS_FILE.is_file():
        errors.append("missing supply-chain/action-pins.yaml")
    if PINS_FILE.is_file():
        try:
            check_external_uses(errors)
        except ValueError as exc:
            errors.append(str(exc))
    check_requirements_lock(errors)
    check_tree_digest(errors)
    if errors:
        print("SUPPLY-CHAIN DIVERGED", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("SUPPLY-CHAIN OK")
    print(f"- action pins: {PINS_FILE.relative_to(ROOT).as_posix()}")
    print(f"- python lock: {LOCK.relative_to(ROOT).as_posix()}")
    print(f"- tree digest files: {len(fingerprint_supply_chain_tree())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

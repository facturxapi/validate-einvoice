#!/usr/bin/env python3
"""Supply-chain gate: pinned third-party actions, hashed Python dependencies, local-action token scope."""

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



def strip_yaml_comment(line: str) -> str:
    """Drop unquoted `#` comments. Quoted `#` is kept.

    Heuristic, not YAML 1.2: GitHub Actions workflows in this repo are
    block-style 2-space mappings. A line like `# actions: write` cannot
    satisfy the permissions gate because it never becomes a key.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for ch in line:
        if in_single:
            out.append(ch)
            if ch == "'":
                in_single = False
            continue
        if in_double:
            out.append(ch)
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_double = False
            continue
        if ch == "#":
            break
        if ch == "'":
            in_single = True
            out.append(ch)
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out).rstrip()


def _unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_flow_mapping(raw: str) -> dict[str, str]:
    inner = raw.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    if not inner:
        return {}
    out: dict[str, str] = {}
    for part in inner.split(","):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        out[_unquote_scalar(key)] = _unquote_scalar(value)
    return out


def _parse_perm_rhs(raw: str) -> dict[str, str] | str | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{") and raw.endswith("}"):
        return _parse_flow_mapping(raw)
    return _unquote_scalar(raw)


def _iter_code_lines(text: str) -> list[tuple[int, int, str]]:
    lines: list[tuple[int, int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        content = strip_yaml_comment(raw)
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        lines.append((lineno, indent, content.strip()))
    return lines


def _collect_block_mapping(
    lines: list[tuple[int, int, str]], start: int, parent_indent: int
) -> tuple[dict[str, str], int]:
    mapping: dict[str, str] = {}
    idx = start
    while idx < len(lines):
        _lineno, indent, content = lines[idx]
        if indent <= parent_indent:
            break
        if ":" not in content:
            idx += 1
            continue
        key, _, rest = content.partition(":")
        mapping[_unquote_scalar(key)] = _unquote_scalar(rest)
        idx += 1
    return mapping, idx


def is_yaml_local_uses_key(content: str) -> bool:
    """True when this YAML key is `uses: ./` (optional sequence dash)."""
    stripped = content.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    if not stripped.startswith("uses:"):
        return False
    after = _unquote_scalar(stripped.split("uses:", 1)[1])
    return after == "./" or after.startswith("./")


def parse_workflow_permissions(text: str) -> dict:
    """Extract top-level and job-level `permissions` plus local `uses: ./`.

    Only `permissions:` at workflow indent 0, or as a direct job key
    (same indent as `runs-on` / `steps`), is counted. Nested keys under
    `steps` / `run` cannot grant Actions mutation. Comments are stripped
    first.
    """
    lines = _iter_code_lines(text)
    top_permissions: dict[str, str] | str | None = None
    jobs: dict[str, dict] = {}
    idx = 0
    while idx < len(lines):
        _lineno, indent, content = lines[idx]
        if indent == 0 and content.startswith("permissions:"):
            parsed = _parse_perm_rhs(content.split(":", 1)[1])
            if parsed is None:
                mapping, idx = _collect_block_mapping(lines, idx + 1, 0)
                top_permissions = mapping
            else:
                top_permissions = parsed
                idx += 1
            continue
        if indent == 0 and (content == "jobs:" or content.startswith("jobs:")):
            idx += 1
            job_indent: int | None = None
            while idx < len(lines) and lines[idx][1] > 0:
                j_lineno, j_indent, j_content = lines[idx]
                if job_indent is None:
                    job_indent = j_indent
                if j_indent == job_indent:
                    job_name = _unquote_scalar(j_content.split(":", 1)[0])
                    job = {
                        "permissions": None,
                        "uses_local": False,
                        "lineno": j_lineno,
                    }
                    jobs[job_name] = job
                    idx += 1
                    body_indent: int | None = None
                    while idx < len(lines) and lines[idx][1] > job_indent:
                        b_lineno, b_indent, b_content = lines[idx]
                        if body_indent is None:
                            body_indent = b_indent
                        if b_indent == body_indent and b_content.startswith(
                            "permissions:"
                        ):
                            parsed = _parse_perm_rhs(b_content.split(":", 1)[1])
                            if parsed is None:
                                mapping, idx = _collect_block_mapping(
                                    lines, idx + 1, b_indent
                                )
                                job["permissions"] = mapping
                            else:
                                job["permissions"] = parsed
                                idx += 1
                            continue
                        if is_yaml_local_uses_key(b_content):
                            job["uses_local"] = True
                        idx += 1
                    continue
                idx += 1
            continue
        idx += 1
    return {
        "top_permissions": top_permissions,
        "jobs": jobs,
        "has_local_uses": any(job["uses_local"] for job in jobs.values()),
    }


def grants_actions_write(perms: dict[str, str] | str | None) -> bool:
    if perms is None:
        return False
    if isinstance(perms, str):
        return perms.strip().lower() in {"write-all", "write"}
    if isinstance(perms, dict):
        for key, value in perms.items():
            if str(key).strip().lower() == "actions" and str(value).strip().lower() == "write":
                return True
    return False


def iter_gha_yaml_files() -> list[Path]:
    files: list[Path] = []
    for folder in (ROOT / ".github" / "workflows", ROOT / "examples"):
        if folder.is_dir():
            files.extend(sorted(folder.glob("*.yml")))
            files.extend(sorted(folder.glob("*.yaml")))
    return files


def check_local_action_permissions(errors: list[str]) -> None:
    """Reject Actions mutation on any workflow/job that `uses: ./`."""
    for path in iter_gha_yaml_files():
        rel = path.relative_to(ROOT).as_posix()
        info = parse_workflow_permissions(path.read_text(encoding="utf-8"))
        if info["has_local_uses"] and grants_actions_write(info["top_permissions"]):
            errors.append(
                f"{rel}: workflow with uses: ./ must not set top-level permissions.actions=write"
            )
        for name, job in info["jobs"].items():
            if job["uses_local"] and grants_actions_write(job["permissions"]):
                errors.append(
                    f"{rel}: job {name!r} uses: ./ must not set permissions.actions=write"
                )


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
    check_local_action_permissions(errors)
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

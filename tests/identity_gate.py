#!/usr/bin/env python3
"""Fail if the delivered tree contains identity or workspace-path needles.

Needles are assembled from byte literals so this file does not itself
contain the forbidden text.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKIP_DIR_NAMES = {".git", ".venv", ".builder", "__pycache__"}

NEEDLES = [
    bytes([0x69, 0x62, 0x72, 0x61, 0x68, 0x69, 0x6D, 0x61]).decode("ascii"),
    bytes([0x63, 0x68, 0x65, 0x72, 0x6F, 0x75, 0x62, 0x61, 0x77, 0x61, 0x6E]).decode("ascii"),
    bytes([0x6E, 0x69, 0x61, 0x73, 0x73]).decode("ascii"),
    bytes([0x49, 0x62, 0x72, 0x61, 0x20]).decode("ascii"),
    bytes([0x68, 0x6F, 0x74, 0x6D, 0x61, 0x69, 0x6C]).decode("ascii"),
    bytes([0x40, 0x67, 0x6D, 0x61, 0x69, 0x6C]).decode("ascii"),
    bytes([0x2F, 0x55, 0x73, 0x65, 0x72, 0x73, 0x2F]).decode("ascii"),
    bytes([0x74, 0x65, 0x6D, 0x70, 0x5F, 0x64, 0x61, 0x74, 0x61]).decode("ascii"),
]


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    hits: list[str] = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"error: cannot read {path.name}: {exc}", file=sys.stderr)
            return 2
        lowered = text.lower()
        for needle in NEEDLES:
            if needle.lower() in lowered or needle in text:
                rel = path.relative_to(root).as_posix()
                hits.append(f"{rel}: matched a forbidden identity needle")
                break
    if hits:
        print("\n".join(hits), file=sys.stderr)
        return 1
    print("identity gate: 0 matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())

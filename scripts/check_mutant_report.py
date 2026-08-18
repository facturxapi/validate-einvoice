#!/usr/bin/env python3
"""Assert a mutant Action step failed with the exact SVRL ids."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if os.environ.get("MUTANT_OUTCOME") != "failure":
        print("mutant job did not fail", file=sys.stderr)
        return 1
    # Windows may inject a trailing CR into GITHUB_OUTPUT values.
    verdict = (os.environ.get("MUTANT_VERDICT") or "").strip()
    if verdict != "fail":
        print("mutant verdict is not fail", file=sys.stderr)
        return 1
    expected = json.loads(Path("testdata/expected-ids.json").read_text(encoding="utf-8"))
    raw = (os.environ.get("MUTANT_REPORT") or "").strip()
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"mutant report is not JSON: {exc}", file=sys.stderr)
        return 1
    got = {Path(row["path"]).name: row["failed_assert_ids"] for row in report["files"]}
    errors = []
    for name, ids in expected.items():
        if got.get(name) != ids:
            errors.append(f"{name}: expected {ids}, got {got.get(name)}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("mutants: exact ids, job failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

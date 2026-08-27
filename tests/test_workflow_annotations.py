#!/usr/bin/env python3
"""Formatter tests for GitHub workflow-command annotations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import validate  # noqa: E402


def runner_decode(line: str) -> str:
    """Inverse of GitHub workflow-command encoding (%0A/%0D first, then %25)."""
    return line.replace("%0A", "\n").replace("%0D", "\r").replace("%25", "%")


class WorkflowAnnotationTests(unittest.TestCase):
    def test_percent_0a_in_svrl_text(self) -> None:
        line = validate.annotation_line(
            "inv.xml", {"id": "BR-01", "text": "x%0A::error::injected"}
        )
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertIn("%250A", line)
        self.assertNotIn("\n", runner_decode(line))
        self.assertEqual(len(line.splitlines()), 1)

    def test_newline_in_filename(self) -> None:
        line = validate.annotation_line(
            "foo\nbar.xml", {"id": "BR-01", "text": "ok"}
        )
        self.assertNotIn("\n", line)
        self.assertIn("file=foo%0Abar.xml", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_percent_0a_in_filename(self) -> None:
        line = validate.annotation_line(
            "foo%0A::error file=pwned::x.xml",
            {"id": "BR-01", "text": "ok"},
        )
        self.assertNotIn("\n", line)
        self.assertIn("%250A", line)
        self.assertNotIn("\n", runner_decode(line))
        self.assertEqual(len(line.splitlines()), 1)

    def test_comma_in_filename(self) -> None:
        line = validate.annotation_line(
            "a,line=1,col=1.xml", {"id": "BR-01", "text": "ok"}
        )
        self.assertIn("file=a%2Cline=1%2Ccol=1.xml", line)
        self.assertNotIn("file=a,line=1", line)
        self.assertNotIn("file=a,line=", line)

    def test_newline_in_rule_id(self) -> None:
        line = validate.annotation_line(
            "inv.xml", {"id": "BR-01\n::warning::x", "text": "ok"}
        )
        self.assertNotIn("\n", line)
        self.assertIn("title=BR-01%0A", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_newline_in_workflow_error_line(self) -> None:
        line = validate.workflow_error_line(
            "no XML files matched: foo\n::error::pwned"
        )
        self.assertTrue(line.startswith("::error::"))
        self.assertNotIn("\n", line)
        self.assertIn("%0A", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_truncate_after_escape(self) -> None:
        text = "%0A" * 80
        line = validate.annotation_line("inv.xml", {"id": "BR-01", "text": text})
        message = line.split("::", 2)[2]
        body = message.split(": ", 1)[1]
        self.assertEqual(len(body), validate.ANNOTATION_TEXT_MAX)
        self.assertTrue(body.endswith("..."))
        self.assertTrue(body.startswith("%250A"))
        self.assertNotIn("\n", line)

    def test_plain_annotation_shape(self) -> None:
        line = validate.annotation_line(
            "inv.xml", {"id": "BR-01", "text": "missing BT-1"}
        )
        self.assertEqual(
            line, "::error file=inv.xml,title=BR-01::BR-01: missing BT-1"
        )


if __name__ == "__main__":
    unittest.main()

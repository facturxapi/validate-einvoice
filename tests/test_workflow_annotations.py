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


def command_parts(line: str) -> tuple[str, str, str]:
    """Split ``::command properties::message`` into (command, properties, message)."""
    if not line.startswith("::"):
        raise ValueError(f"not a workflow command: {line!r}")
    header, sep, message = line[2:].partition("::")
    if not sep:
        raise ValueError(f"missing command terminator: {line!r}")
    if " " in header:
        command, properties = header.split(" ", 1)
    else:
        command, properties = header, ""
    return command, properties, message


def property_values(properties: str) -> dict[str, str]:
    """Parse ``file=…,title=…`` without treating encoded commas as separators."""
    out: dict[str, str] = {}
    for piece in properties.split(","):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        out[key] = value
    return out


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

    def test_filename_double_colon_warning(self) -> None:
        """Causal repro: ``a::warning::b.xml`` must not open a new command."""
        line = validate.annotation_line(
            "a::warning::b.xml", {"id": "BR-01", "text": "ok"}
        )
        command, props, message = command_parts(line)
        self.assertEqual(command, "error")
        self.assertEqual(len(line.split("::")), 3)
        self.assertIn("file=a%3A%3Awarning%3A%3Ab.xml", props)
        self.assertNotIn("file=a::warning::", line)
        header = line[: line.index("::", 2)]
        self.assertNotIn("::warning::", header)
        values = property_values(props)
        self.assertNotIn(":", values["file"])
        self.assertNotIn(",", values["file"])

    def test_filename_colon(self) -> None:
        line = validate.annotation_line(
            "in:voice.xml", {"id": "BR-01", "text": "ok"}
        )
        _, props, _ = command_parts(line)
        self.assertIn("file=in%3Avoice.xml", props)
        self.assertNotIn("file=in:voice.xml", props)
        self.assertNotIn(":", property_values(props)["file"])

    def test_rule_id_colon_in_title(self) -> None:
        line = validate.annotation_line(
            "inv.xml", {"id": "BR:01", "text": "missing BT-1"}
        )
        _, props, message = command_parts(line)
        self.assertIn("title=BR%3A01", props)
        self.assertNotIn("title=BR:01", props)
        self.assertNotIn(":", property_values(props)["title"])
        self.assertTrue(message.startswith("BR:01: "))

    def test_rule_id_comma_in_title(self) -> None:
        line = validate.annotation_line(
            "inv.xml", {"id": "BR-01,x", "text": "ok"}
        )
        _, props, message = command_parts(line)
        self.assertIn("title=BR-01%2Cx", props)
        self.assertNotIn("title=BR-01,x", props)
        self.assertNotIn(",", property_values(props)["title"])
        self.assertTrue(message.startswith("BR-01,x: "))

    def test_combo_percent_newline_colon_comma(self) -> None:
        filename = "a%:\n,b.xml"
        rule_id = "BR:%\n,01"
        text = "x%:\n,y"
        line = validate.annotation_line(filename, {"id": rule_id, "text": text})
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertEqual(len(line.splitlines()), 1)
        _, props, message = command_parts(line)
        self.assertEqual(property_values(props)["file"], "a%25%3A%0A%2Cb.xml")
        self.assertEqual(property_values(props)["title"], "BR%3A%25%0A%2C01")
        for value in property_values(props).values():
            self.assertNotIn(":", value)
            self.assertNotIn(",", value)
        # B: user text encodes % and newline only — raw : and , remain.
        self.assertTrue(message.startswith("BR:%25%0A,01: "))
        self.assertIn("x%25:%0A,y", message)

    def test_user_message_keeps_colon_and_comma(self) -> None:
        line = validate.annotation_line(
            "inv.xml",
            {"id": "CII:BR-01", "text": "missing BT-1, field X"},
        )
        _, props, message = command_parts(line)
        self.assertIn("title=CII%3ABR-01", props)
        self.assertTrue(message.startswith("CII:BR-01: "))
        self.assertIn("missing BT-1, field X", message)
        self.assertNotIn("%3A", message)
        self.assertNotIn("%2C", message)

    def test_header_cannot_be_broken(self) -> None:
        line = validate.annotation_line(
            "a::warning::b.xml",
            {"id": "BR:01,x", "text": "ok: still, readable"},
        )
        parts = line.split("::")
        self.assertEqual(len(parts), 3, line)
        self.assertEqual(parts[0], "")
        self.assertTrue(parts[1].startswith("error "))
        self.assertIn("file=a%3A%3Awarning%3A%3Ab.xml", parts[1])
        self.assertIn("title=BR%3A01%2Cx", parts[1])
        _, props, message = command_parts(line)
        for value in property_values(props).values():
            self.assertNotIn(":", value)
            self.assertNotIn(",", value)
        self.assertEqual(message, "BR:01,x: ok: still, readable")


if __name__ == "__main__":
    unittest.main()

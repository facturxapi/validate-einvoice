#!/usr/bin/env python3
"""Offline tests for the upstream-drift selector."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_upstream  # noqa: E402


def pins() -> dict:
    return json.loads((ROOT / "vendor" / "upstream.json").read_text(encoding="utf-8"))


class SelectLatestTests(unittest.TestCase):
    def test_prefers_newest_cii_ubl_and_skips_edifact_only(self) -> None:
        releases = [
            {
                "tag_name": "validation-1.0.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2018-02-08T00:00:00Z",
                "html_url": "https://example.invalid/1.0.0",
                "assets": [{"name": "en16931-edifact-1.0.0.zip", "browser_download_url": "https://example.invalid/edifact.zip"}],
            },
            {
                "tag_name": "validation-1.3.15",
                "draft": False,
                "prerelease": False,
                "published_at": "2025-10-20T14:36:27Z",
                "html_url": "https://example.invalid/1.3.15",
                "assets": [
                    {"name": "en16931-cii-1.3.15.zip", "browser_download_url": "https://example.invalid/cii-15.zip"},
                    {"name": "en16931-ubl-1.3.15.zip", "browser_download_url": "https://example.invalid/ubl-15.zip"},
                ],
            },
            {
                "tag_name": "validation-1.3.16",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-04-13T12:58:43Z",
                "html_url": "https://example.invalid/1.3.16",
                "assets": [
                    {"name": "en16931-cii-1.3.16.zip", "browser_download_url": "https://example.invalid/cii-16.zip"},
                    {"name": "en16931-ubl-1.3.16.zip", "browser_download_url": "https://example.invalid/ubl-16.zip"},
                ],
            },
        ]
        latest = check_upstream.select_latest(releases, pins())
        self.assertEqual(latest["tag"], "validation-1.3.16")
        self.assertEqual(latest["version"], "1.3.16")
        self.assertIn("cii", latest["assets"])
        self.assertIn("ubl", latest["assets"])

    def test_skips_prerelease(self) -> None:
        releases = [
            {
                "tag_name": "validation-9.9.9",
                "draft": False,
                "prerelease": True,
                "published_at": "2027-01-01T00:00:00Z",
                "assets": [
                    {"name": "en16931-cii-9.9.9.zip", "browser_download_url": "https://example.invalid/cii.zip"},
                    {"name": "en16931-ubl-9.9.9.zip", "browser_download_url": "https://example.invalid/ubl.zip"},
                ],
            },
            {
                "tag_name": "validation-1.3.16",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-04-13T12:58:43Z",
                "html_url": "https://example.invalid/1.3.16",
                "assets": [
                    {"name": "en16931-cii-1.3.16.zip", "browser_download_url": "https://example.invalid/cii-16.zip"},
                    {"name": "en16931-ubl-1.3.16.zip", "browser_download_url": "https://example.invalid/ubl-16.zip"},
                ],
            },
        ]
        latest = check_upstream.select_latest(releases, pins())
        self.assertEqual(latest["tag"], "validation-1.3.16")

    def test_compare_detects_new_tag(self) -> None:
        latest = {"tag": "validation-1.3.17", "html_url": ""}
        measured = {
            "cii": {"zip_sha256": "aa", "xslt_sha256": pins()["assets"]["cii"]["xslt_sha256"]},
            "ubl": {"zip_sha256": "bb", "xslt_sha256": pins()["assets"]["ubl"]["xslt_sha256"]},
        }
        diverged = check_upstream.compare(pins(), latest, measured)
        self.assertTrue(any("release tag" in line for line in diverged))

    def test_payload_must_be_a_list(self) -> None:
        with self.assertRaises(check_upstream.InstrumentFailure):
            check_upstream.parse_releases({"message": "not a list"})


if __name__ == "__main__":
    unittest.main()

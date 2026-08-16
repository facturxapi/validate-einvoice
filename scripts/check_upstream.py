#!/usr/bin/env python3
"""Compare vendored EN16931 XSLT pins to the latest ConnectingEurope CII/UBL release.

Does not rewrite pins. Network or API failure is an instrument failure (exit 2),
never a silent pass. A newer tag or a SHA mismatch is drift (exit 1).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PIN = ROOT / "vendor" / "upstream.json"
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INSTRUMENT = 2
TIMEOUT_S = 60
TAG_RE = re.compile(r"^validation-(.+)$")


class InstrumentFailure(Exception):
    """Upstream could not be observed."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pins(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstrumentFailure(f"cannot read pin file {path.name}: {exc}") from exc


def http_get(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstrumentFailure(f"network/API failure fetching {url}: {exc}") from exc


def parse_releases(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise InstrumentFailure("GitHub releases payload is not a list")
    return payload


def cii_ubl_release(item: dict[str, Any], pins: dict[str, Any]) -> dict[str, Any] | None:
    tag = item.get("tag_name")
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        return None
    if item.get("draft") or item.get("prerelease"):
        return None
    assets = item.get("assets") or []
    found: dict[str, dict[str, str]] = {}
    for key, spec in pins["assets"].items():
        prefix = spec["name_prefix"]
        match = None
        for asset in assets:
            name = asset.get("name") or ""
            if name.startswith(prefix) and name.endswith(".zip"):
                match = asset
                break
        if match is None:
            return None
        url = match.get("browser_download_url")
        if not url:
            return None
        found[key] = {"name": match["name"], "url": url}
    return {
        "tag": tag,
        "version": TAG_RE.match(tag).group(1),
        "published_at": item.get("published_at") or "",
        "html_url": item.get("html_url") or "",
        "assets": found,
    }


def select_latest(releases: list[dict[str, Any]], pins: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in releases:
        if not isinstance(item, dict):
            continue
        parsed = cii_ubl_release(item, pins)
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        raise InstrumentFailure(
            "no ConnectingEurope release with both CII and UBL zip assets"
        )
    candidates.sort(key=lambda row: row["published_at"], reverse=True)
    return candidates[0]


def download_and_hash(url: str, user_agent: str, inner: str) -> tuple[str, str]:
    blob = http_get(url, user_agent)
    zip_digest = sha256_bytes(blob)
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            try:
                xslt = archive.read(inner)
            except KeyError as exc:
                raise InstrumentFailure(f"zip from {url} has no {inner}") from exc
    except zipfile.BadZipFile as exc:
        raise InstrumentFailure(f"invalid zip from {url}: {exc}") from exc
    return zip_digest, sha256_bytes(xslt)


def write_summary(lines: list[str]) -> None:
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if dest:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write(text)


def compare(pins: dict[str, Any], latest: dict[str, Any], measured: dict[str, dict[str, str]]) -> list[str]:
    diverged: list[str] = []
    if latest["tag"] != pins["pinned_tag"]:
        diverged.append(
            f"release tag: upstream={latest['tag']} pinned={pins['pinned_tag']}"
        )
    for key, spec in pins["assets"].items():
        got = measured[key]
        if latest["tag"] == pins["pinned_tag"] and got["zip_sha256"] != spec["zip_sha256"]:
            diverged.append(
                f"{key} zip SHA256: upstream={got['zip_sha256']} pinned={spec['zip_sha256']}"
            )
        if got["xslt_sha256"] != spec["xslt_sha256"]:
            diverged.append(
                f"{spec['xslt_inner']}: upstream={got['xslt_sha256']} pinned={spec['xslt_sha256']}"
            )
    return diverged


def main(argv: list[str] | None = None) -> int:
    del argv
    pin_path = Path(os.environ.get("EN16931_UPSTREAM_PIN", DEFAULT_PIN))
    try:
        pins = load_pins(pin_path)
        user_agent = pins.get("user_agent") or "en16931-validate-einvoice-upstream-check/1.0"
        raw = http_get(pins["releases_api"], user_agent)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstrumentFailure(f"releases API did not return JSON: {exc}") from exc
        latest = select_latest(parse_releases(payload), pins)
        measured: dict[str, dict[str, str]] = {}
        for key, spec in pins["assets"].items():
            zip_sha, xslt_sha = download_and_hash(
                latest["assets"][key]["url"], user_agent, spec["xslt_inner"]
            )
            measured[key] = {
                "asset": latest["assets"][key]["name"],
                "zip_sha256": zip_sha,
                "xslt_sha256": xslt_sha,
            }
        diverged = compare(pins, latest, measured)
    except InstrumentFailure as exc:
        write_summary(
            [
                "## EN16931 upstream check — instrument failure",
                "",
                str(exc),
                "",
                "This is not a green 'no drift'. The probe could not observe upstream.",
            ]
        )
        return EXIT_INSTRUMENT

    header = [
        "## EN16931 upstream check",
        "",
        f"- Pinned tag: `{pins['pinned_tag']}`",
        f"- Upstream CII/UBL tag: `{latest['tag']}`",
        f"- Upstream URL: {latest['html_url']}",
    ]
    for key, spec in pins["assets"].items():
        got = measured[key]
        header.append(
            f"- `{got['asset']}` XSLT `{spec['xslt_inner']}`: `{got['xslt_sha256']}`"
        )
    if diverged:
        write_summary(
            header
            + [
                "",
                "**Drift detected. Pins were not updated.**",
                "",
                "Divergent fields:",
                "",
                *[f"- {line}" for line in diverged],
            ]
        )
        return EXIT_DRIFT
    write_summary(header + ["", "No drift: tag and XSLT SHA256 match the vendored pins."])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

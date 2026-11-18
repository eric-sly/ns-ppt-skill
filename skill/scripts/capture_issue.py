#!/usr/bin/env python3
"""Capture a journal issue as MHTML with a real browser (agent acquisition).

Two acquisition modes are supported by this skill:

  * user-manual-save — the user saves each page with the browser's
    "Save page as, single file (*.mhtml)";
  * agent-capture — this script drives the installed Chrome/Edge over the
    DevTools protocol, expands the controls the page needs, scrolls the whole
    issue so lazy content loads, and snapshots the page as MHTML
    (`Page.captureSnapshot`). The MHTML stays the single downstream source;
    nothing is parsed from a live URL.

Usage:

    python capture_issue.py --journal nature  --out-dir work/capture
    python capture_issue.py --journal science --out-dir work/capture

The script writes one MHTML per role plus `capture-manifest.json`:

    nature:  nature-issue.mhtml
    science: science-toc.mhtml, in-science-journals.mhtml, in-other-journals.mhtml

Pass the manifest to the scrape stage (`--capture-manifest`) so issue.json
records `agent-capture` provenance and verifies the file hashes.

Requirements: `pip install playwright`, plus an installed Chrome or Edge
(launched through `channel="chrome"` / `"msedge"`; no browser download needed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0"

NATURE_TOC = "https://www.nature.com/nature/current-issue"
SCIENCE_TOC = "https://www.science.org/toc/science/current"

NATURE_EXPAND_SELECTOR = ".app-promo-text__button"
NATURE_EXPANDED_TEXT = "show less"
SCIENCE_EXPAND_SELECTOR = ".journal-issue__details .view-more"
SCIENCE_EXPANDED_TEXT = "view less"

COOKIE_LABELS = ("Reject optional", "Reject all", "Accept all")

# Below these counts the capture is almost certainly incomplete (a consent
# wall, a login prompt, or a page that never rendered).  They are warnings, not
# hard failures: the operator decides whether to accept the capture.
NATURE_MIN_CARDS = 20
SCIENCE_MIN_SECTIONS = 3


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - depends on the host environment
        raise SystemExit(
            "playwright is not installed for this interpreter.\n"
            "  pip install playwright\n"
            "No browser download is needed when Chrome or Edge is installed; the script "
            "launches them with --channel chrome (or msedge)."
        )
    return sync_playwright


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _dismiss_cookies(page) -> str | None:
    for _ in range(3):
        for label in COOKIE_LABELS:
            try:
                button = page.get_by_role("button", name=label, exact=False)
                if button.count():
                    button.first.click(timeout=3000)
                    return label
            except Exception:
                continue
        page.wait_for_timeout(400)
    return None


def _expand(page, selector: str, done_text: str, max_clicks: int = 8) -> list[str]:
    labels: list[str] = []
    missed = 0
    for _ in range(max_clicks):
        try:
            control = page.locator(selector)
            if not control.count():
                # the promo block is client-rendered; retry instead of giving up
                missed += 1
                if missed > 6:
                    break
                page.wait_for_timeout(500)
                continue
            missed = 0
            label = _norm(control.first.inner_text())
            if labels and labels[-1] == label:
                page.wait_for_timeout(400)
            labels.append(label)
            if done_text in label:
                break
            control.first.click(timeout=5000)
            page.wait_for_timeout(700)
        except Exception:
            break
    return labels


def _scroll_through(page, passes: int = 3) -> int:
    height = 0
    for _ in range(passes):
        height = page.evaluate("document.documentElement.scrollHeight")
        step = 600
        offset = 0
        while offset < height:
            page.evaluate(f"window.scrollTo(0, {offset})")
            page.wait_for_timeout(110)
            offset += step
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(700)
        grown = page.evaluate("document.documentElement.scrollHeight")
        if grown <= height:
            break
        height = grown
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)
    return height


def _wait_ready(page, selector: str, timeout_ms: int) -> None:
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
    except Exception:
        pass


def _diagnostics(page, journal: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "url": page.url,
        "title": page.title(),
        "bytes_rendered": page.evaluate("document.documentElement.scrollHeight"),
    }
    if journal == "nature":
        data["cards"] = page.locator(".c-card").count()
        data["sections"] = page.locator("section[id]").count()
    else:
        data["cards"] = page.locator("h3.article-title").count()
        data["sections"] = page.locator("section.toc__section").count()
    return data


def _snapshot(page, out_path: Path) -> dict[str, Any]:
    session = page.context.new_cdp_session(page)
    payload = session.send("Page.captureSnapshot", {"format": "mhtml"})["data"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # bytes, not text mode: the snapshot already carries \r\n and text mode would
    # turn every \n into \r\n, producing \r\r\n and breaking the MIME header parse.
    out_path.write_bytes(payload.encode("utf-8"))
    raw = out_path.read_bytes()
    return {
        "path": str(out_path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _capture_one(page, url: str, out_path: Path, journal: str,
                 expand_selector: str, expanded_text: str, timeout_ms: int,
                 is_toc: bool = True) -> dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if is_toc:
        _wait_ready(page, ".c-card, .article-title", timeout_ms)
        _wait_ready(page, expand_selector, timeout_ms)
    else:
        _wait_ready(page, "section[id^=sec-], .core-label", timeout_ms)
    cookie = _dismiss_cookies(page)
    labels = _expand(page, expand_selector, expanded_text)
    _scroll_through(page)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    result = _diagnostics(page, journal)
    result.update(_snapshot(page, out_path))
    result["requested_url"] = url
    result["expansion_labels"] = labels
    result["expansion_state"] = labels[-1] if labels else None
    result["cookie_action"] = cookie
    return result


def _digest_urls(page) -> dict[str, str]:
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({text: e.innerText || e.textContent || '', href: e.href}))",
    )
    wanted = {"in_science": "in science journals", "in_other": "in other journals"}
    found: dict[str, str] = {}
    for key, needle in wanted.items():
        for link in links:
            if needle in _norm(link["text"]):
                found[key] = link["href"]
                break
    return found


def _launch(playwright, channel: str | None, headless: bool):
    channels = [channel] if channel else ["chrome", "msedge", ""]
    last_error: Exception | None = None
    for name in channels:
        try:
            kwargs: dict[str, Any] = {"headless": headless, "args": ["--lang=en-US"]}
            if name:
                kwargs["channel"] = name
            browser = playwright.chromium.launch(**kwargs)
            return browser, name or "bundled-chromium"
        except Exception as error:  # channel not installed
            last_error = error
            continue
    raise SystemExit(f"could not launch a Chromium-family browser: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--journal", choices=("nature", "science"), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--toc-url", help=f"TOC URL override (default: {NATURE_TOC} / {SCIENCE_TOC})")
    parser.add_argument("--in-science-url", help="Science: In Science Journals detail URL if the TOC link is not found")
    parser.add_argument("--in-other-url", help="Science: In Other Journals detail URL if the TOC link is not found")
    parser.add_argument("--channel", help="browser channel: chrome, msedge, chromium (default: try chrome, then msedge, then bundled)")
    parser.add_argument("--headed", action="store_true", help="show the browser window (use when a consent wall needs a human)")
    parser.add_argument("--timeout", type=float, default=90.0, help="navigation timeout in seconds (default 90)")
    args = parser.parse_args()

    timeout_ms = int(args.timeout * 1000)
    toc_url = args.toc_url or (NATURE_TOC if args.journal == "nature" else SCIENCE_TOC)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    sync_playwright = _playwright()
    warnings: list[str] = []
    files: dict[str, dict[str, Any]] = {}

    with sync_playwright() as playwright:
        browser, channel_used = _launch(playwright, args.channel, not args.headed)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            timezone_id="Europe/London",
        )
        page = context.new_page()

        if args.journal == "nature":
            files["nature"] = _capture_one(
                page, toc_url, out_dir / "nature-issue.mhtml", "nature",
                NATURE_EXPAND_SELECTOR, NATURE_EXPANDED_TEXT, timeout_ms,
            )
            if NATURE_EXPANDED_TEXT not in _norm(str(files["nature"].get("expansion_state"))):
                warnings.append("cover description did not reach '" + NATURE_EXPANDED_TEXT + "'")
            if files["nature"].get("cards", 0) < NATURE_MIN_CARDS:
                warnings.append(f"only {files['nature'].get('cards')} cards captured")
        else:
            files["science_toc"] = _capture_one(
                page, toc_url, out_dir / "science-toc.mhtml", "science",
                SCIENCE_EXPAND_SELECTOR, SCIENCE_EXPANDED_TEXT, timeout_ms,
            )
            if SCIENCE_EXPANDED_TEXT not in _norm(str(files["science_toc"].get("expansion_state"))):
                warnings.append("issue description did not reach '" + SCIENCE_EXPANDED_TEXT + "'")
            if files["science_toc"].get("sections", 0) < SCIENCE_MIN_SECTIONS:
                warnings.append(f"only {files['science_toc'].get('sections')} TOC sections captured")

            digests = _digest_urls(page)
            if args.in_science_url:
                digests["in_science"] = args.in_science_url
            if args.in_other_url:
                digests["in_other"] = args.in_other_url
            for role, filename in (("in_science", "in-science-journals.mhtml"),
                                   ("in_other", "in-other-journals.mhtml")):
                url = digests.get(role)
                if not url:
                    raise SystemExit(
                        f"could not find the {role} detail link on the TOC; pass "
                        f"--{role.replace('_', '-')}-url explicitly"
                    )
                detail = context.new_page()
                files[role] = _capture_one(
                    detail, url, out_dir / filename, "science", "", "", timeout_ms, is_toc=False,
                )
                files[role]["requested_url"] = url
                detail.close()

        browser.close()

    manifest = {
        "journal": args.journal,
        "tool": "capture_issue.py",
        "tool_version": TOOL_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel_used,
        "headless": not args.headed,
        "files": files,
    }
    if warnings:
        manifest["warnings"] = warnings
    manifest_path = out_dir / "capture-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path.resolve()), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
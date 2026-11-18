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

Failure is a normal outcome, not an exception to hide: a bot-protection or
consent screen, a page with far too few items, or a digest link that cannot be
found makes the script exit 3 and mark the manifest `status: failed`, with the
suspect file moved to `rejected/` and a ready-to-send `message_for_user` naming
the exact pages, URLs, save paths and steps. The agent must then stop and ask the
user to save those pages (user-manual-save), never scrape a rejected page.

Requirements: the `playwright` package must be importable by the interpreter
running this script, and Chrome or Edge must be installed (launched through
`channel="chrome"` / `"msedge"`). The skill does not bundle playwright and does
not prescribe how to install it: when the package is missing the script fails
over to the user (see below), and whether to install it is a decision for the
user and the agent to make in that session.
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

# Titles served by bot protection / consent walls instead of the issue page.
CHALLENGE_TITLES = (
    "checking your browser",
    "just a moment",
    "attention required",
    "are you a robot",
    "security verification",
    "performing security verification",
    "access denied",
    "verify you are human",
)

SAVE_STEPS = {
    "nature": (
        "Expand the cover description until the control reads 'show less'.",
        "Scroll through the whole issue so every item and image loads.",
        "Save the page as a single-file MHTML (网页，单个文件 (*.mhtml)).",
    ),
    "science_toc": (
        "Expand the issue description until the control reads 'View Less'.",
        "Scroll through the whole issue so every item and image loads.",
        "Save the page as a single-file MHTML (网页，单个文件 (*.mhtml)).",
    ),
    "in_science": (
        "Scroll through every entry on the page.",
        "Save the page as a single-file MHTML (网页，单个文件 (*.mhtml)).",
    ),
    "in_other": (
        "Scroll through every entry on the page.",
        "Save the page as a single-file MHTML (网页，单个文件 (*.mhtml)).",
    ),
}

# Below these counts the capture is almost certainly incomplete (a consent
# wall, a login prompt, or a page that never rendered).  They are warnings, not
# hard failures: the operator decides whether to accept the capture.
NATURE_MIN_CARDS = 20
SCIENCE_MIN_SECTIONS = 3


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - depends on the host environment
        raise RuntimeError(
            "browser automation needs the playwright package, which is not available in the "
            "Python interpreter running this skill"
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
    lower = _norm(data["title"])
    data["challenge"] = next((marker for marker in CHALLENGE_TITLES if marker in lower), None)
    data["body_text"] = _norm(page.evaluate("document.body ? document.body.innerText.slice(0, 400) : ''"))
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
    raise RuntimeError(f"could not launch a Chromium-family browser: {last_error}")


def _role_targets(journal: str, toc_url: str, out_dir: Path,
                  in_science_url: str | None, in_other_url: str | None):
    if journal == "nature":
        return [("nature", toc_url, out_dir / "nature-issue.mhtml")]
    return [
        ("science_toc", toc_url, out_dir / "science-toc.mhtml"),
        ("in_science", in_science_url or "", out_dir / "in-science-journals.mhtml"),
        ("in_other", in_other_url or "", out_dir / "in-other-journals.mhtml"),
    ]


def _emit_failure(out_dir: Path, journal: str, failures: list[dict[str, Any]],
                  channel: str | None, headless: bool,
                  warnings: list[str] | None = None) -> int:
    manifest = {
        "journal": journal,
        "tool": "capture_issue.py",
        "tool_version": TOOL_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel,
        "headless": headless,
        "status": "failed",
        "files": {},
        "failures": failures,
        "message_for_user": _request_message(journal, failures),
    }
    if warnings:
        manifest["warnings"] = warnings
    manifest_path = out_dir / "capture-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path.resolve()), **manifest}, ensure_ascii=False, indent=2))
    return 3


def _request_message(journal: str, failures: list[dict[str, Any]]) -> str:
    """A ready-to-send request the agent can hand to the user (translate as needed)."""
    labels = {
        "nature": "the Nature issue table of contents",
        "science_toc": "the Science issue table of contents",
        "in_science": "the 'In Science Journals' page",
        "in_other": "the 'In Other Journals' page",
    }
    lines = [
        f"Automatic capture did not work for this {journal.capitalize()} issue, so the source pages "
        "cannot be obtained by the agent. Please save the file(s) below yourself:",
        "",
    ]
    advices = [failure["advice"] for failure in failures if failure.get("advice")]
    if advices:
        lines.insert(0, advices[0])
        lines.insert(1, "")
    for index, failure in enumerate(failures, 1):
        lines.append(f"{index}) {labels.get(failure['role'], failure['role'])}")
        if failure.get("url"):
            lines.append(f"   URL: {failure['url']}")
        lines.append(f"   Why the agent stopped: {failure['reason']}")
        lines.append(f"   Save to: {failure['save_to']}")
        lines.append("   Steps:")
        for step in failure["steps"]:
            lines.append(f"     - {step}")
        lines.append("")
    lines.append("When the files exist, tell the agent their paths. Do not pass --capture-manifest "
                 "for files you saved yourself; the agent will record them as user-manual-save.")
    return "\n".join(lines)


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

    sync_playwright = None
    try:
        sync_playwright = _playwright()
    except RuntimeError as error:
        advice = (
            "This skill does not bundle playwright and does not prescribe how to install it. "
            "Ask the user whether to make playwright available to this Python interpreter "
            "(their choice of method) or to save the pages below themselves."
        )
        failures = [
            {
                "role": role,
                "reason": f"browser automation is unavailable: {error}",
                "url": url or None,
                "save_to": str(path.resolve()),
                "steps": list(SAVE_STEPS[role]),
                "advice": advice,
            }
            for role, url, path in _role_targets(args.journal, toc_url, out_dir,
                                                 args.in_science_url, args.in_other_url)
        ]
        return _emit_failure(out_dir, args.journal, failures, None, not args.headed)

    warnings: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    def reject_reason(role: str, result: dict[str, Any]) -> str | None:
        if result.get("challenge"):
            return f"the site served a bot-protection/consent screen (title: {result.get('title')!r})"
        if role == "nature":
            if result.get("cards", 0) < NATURE_MIN_CARDS:
                return f"only {result.get('cards')} cards found, expected at least {NATURE_MIN_CARDS}"
        elif role == "science_toc":
            if result.get("sections", 0) < SCIENCE_MIN_SECTIONS:
                return f"only {result.get('sections')} TOC sections found, expected at least {SCIENCE_MIN_SECTIONS}"
        else:
            if result.get("sections", 0) < 1:
                return "no digest entries found on the detail page"
        return None

    def record_failure(role: str, reason: str, url: str, path: Path,
                       advice: str | None = None) -> None:
        entry = {
            "role": role,
            "reason": reason,
            "url": url or None,
            "save_to": str(path.resolve()),
            "steps": list(SAVE_STEPS[role]),
        }
        if advice:
            entry["advice"] = advice
        failures.append(entry)

    with sync_playwright() as playwright:
        try:
            browser, channel_used = _launch(playwright, args.channel, not args.headed)
        except RuntimeError as error:
            advice = (
                "No Chrome/Edge window could be launched. Ask the user whether to point the "
                "script at a browser (--channel) or to save the pages below themselves."
            )
            failures = [
                {
                    "role": role,
                    "reason": str(error),
                    "url": url or None,
                    "save_to": str(path.resolve()),
                    "steps": list(SAVE_STEPS[role]),
                    "advice": advice,
                }
                for role, url, path in _role_targets(args.journal, toc_url, out_dir,
                                                     args.in_science_url, args.in_other_url)
            ]
            return _emit_failure(out_dir, args.journal, failures, args.channel, not args.headed)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            timezone_id="Europe/London",
        )
        page = context.new_page()

        if args.journal == "nature":
            target = out_dir / "nature-issue.mhtml"
            result = _capture_one(
                page, toc_url, target, "nature",
                NATURE_EXPAND_SELECTOR, NATURE_EXPANDED_TEXT, timeout_ms,
            )
            result["requested_url"] = toc_url
            files["nature"] = result
            reason = reject_reason("nature", result)
            if reason:
                record_failure("nature", reason, toc_url, target)
            elif NATURE_EXPANDED_TEXT not in _norm(str(result.get("expansion_state"))):
                warnings.append("cover description did not reach '" + NATURE_EXPANDED_TEXT + "'")
        else:
            toc_target = out_dir / "science-toc.mhtml"
            toc_result = _capture_one(
                page, toc_url, toc_target, "science",
                SCIENCE_EXPAND_SELECTOR, SCIENCE_EXPANDED_TEXT, timeout_ms,
            )
            toc_result["requested_url"] = toc_url
            files["science_toc"] = toc_result
            toc_reason = reject_reason("science_toc", toc_result)
            if toc_reason:
                record_failure("science_toc", toc_reason, toc_url, toc_target)
                for role, filename in (("in_science", "in-science-journals.mhtml"),
                                       ("in_other", "in-other-journals.mhtml")):
                    record_failure(role, "the TOC capture failed, so this detail link could not be "
                                         "discovered; open it from the TOC yourself",
                                   args.in_science_url if role == "in_science" else args.in_other_url,
                                   out_dir / filename)
            else:
                if SCIENCE_EXPANDED_TEXT not in _norm(str(toc_result.get("expansion_state"))):
                    warnings.append("issue description did not reach '" + SCIENCE_EXPANDED_TEXT + "'")
                digests = _digest_urls(page)
                if args.in_science_url:
                    digests["in_science"] = args.in_science_url
                if args.in_other_url:
                    digests["in_other"] = args.in_other_url
                for role, filename in (("in_science", "in-science-journals.mhtml"),
                                       ("in_other", "in-other-journals.mhtml")):
                    url = digests.get(role)
                    target = out_dir / filename
                    if not url:
                        record_failure(role, "the digest link was not found on the TOC", "", target)
                        continue
                    detail = context.new_page()
                    result = _capture_one(
                        detail, url, target, "science", "", "", timeout_ms, is_toc=False,
                    )
                    result["requested_url"] = url
                    detail.close()
                    reason = reject_reason(role, result)
                    if reason:
                        record_failure(role, reason, url, target)
                    else:
                        files[role] = result

        browser.close()

    if failures:
        # Never leave a challenge page behind as if it were a usable source.
        rejected_dir = out_dir / "rejected"
        failed_roles = {failure["role"] for failure in failures}
        for role in failed_roles:
            captured = files.get(role) or {}
            path = Path(captured.get("path", "")) if captured.get("path") else None
            if path and path.is_file():
                rejected_dir.mkdir(parents=True, exist_ok=True)
                path.replace(rejected_dir / path.name)
            files.pop(role, None)
        return _emit_failure(out_dir, args.journal, failures, channel_used,
                             not args.headed, warnings)

    manifest = {
        "journal": args.journal,
        "tool": "capture_issue.py",
        "tool_version": TOOL_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel_used,
        "headless": not args.headed,
        "status": "ok",
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
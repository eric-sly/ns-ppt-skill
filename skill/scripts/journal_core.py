#!/usr/bin/env python3
"""Shared core for the Nature-only and Science-only journal guide pipelines.

Owned by scripts/run_nature_only.py and scripts/run_science_only.py. This module
contains the MHTML parsing, deterministic PPTX building, structural audit, and
environment-gate helpers shared by both entry scripts. It is not meant to be
run directly.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import mimetypes
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag
from PIL import Image

# ---------------------------------------------------------------------------
# Namespaces and shared constants
# ---------------------------------------------------------------------------

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"a": A_NS, "p": P_NS}
A = f"{{{A_NS}}}"
P = f"{{{P_NS}}}"
R = f"{{{R_NS}}}"
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

WHITE = "FFFFFF"
YELLOW = "FFC000"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
NATURE_TITLE_ONLY_TYPES = {
    "Book Review",
    "Correspondence",
    "Career Column",
    "Career Feature",
    "Technology Feature",
    "Correction",
    "Author Correction",
    "Publisher Correction",
    "Retraction Note",
}

# ---------------------------------------------------------------------------
# Text helpers and source loading
# ---------------------------------------------------------------------------


@dataclass
class Source:
    html: str
    url: str
    resources: dict[str, tuple[str, bytes]]


def clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def doi_from(value: str) -> str:
    match = DOI_RE.search(value)
    return match.group(0).rstrip(".)],;") if match else ""


def sentences(value: str) -> list[str]:
    return [clean(part) for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", value) if clean(part)]


def abs_url(base: str, href: str | None) -> str:
    return urllib.parse.urljoin(base, href or "")


def load_source(value: str) -> Source:
    path = Path(value)
    if path.suffix.lower() not in {".mhtml", ".mht"}:
        raise ValueError(f"only local user-saved MHTML is allowed: {value}")
    raw = path.read_bytes()

    message = BytesParser(policy=policy.default).parsebytes(raw)
    pages: list[tuple[int, str, str]] = []
    resources: dict[str, tuple[str, bytes]] = {}
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        location = clean(part.get("Content-Location"))
        content_type = part.get_content_type()
        if location:
            resources[location] = (content_type, payload)
        if content_type == "text/html" and payload:
            pages.append((len(payload), location, payload.decode("utf-8", errors="replace")))
    if not pages:
        raise ValueError(f"no HTML part found in {value}")
    _, location, html = max(pages, key=lambda item: item[0])
    return Source(html, location or value, resources)


def write_issue(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _authors(card: Tag) -> list[str]:
    result: list[str] = []
    for node in card.select(".card-contribs span, .app-author-list li, .app-author-list span"):
        value = clean(node.get_text(" ", strip=True))
        if value and value not in result and value not in {"BY", "ET AL."}:
            result.append(value)
    return result


def _write_cover(issue: dict[str, Any], source: Source, directory: Path, stem: str) -> None:
    url = issue["cover"].get("image_url", "")
    if not url:
        return
    payload: bytes | None = None
    content_type = ""
    if url in source.resources:
        content_type, payload = source.resources[url]
    else:
        for location, resource in source.resources.items():
            if location.split("?", 1)[0] == url.split("?", 1)[0]:
                content_type, payload = resource
                break
    if payload is None:
        raise ValueError(
            f"cover image not found in MHTML resources for {stem}; "
            "agent download is forbidden — re-save the MHTML after scrolling "
            "the issue page fully so the cover image is embedded"
        )
    ext = mimetypes.guess_extension(content_type.split(";", 1)[0]) or Path(urllib.parse.urlparse(url).path).suffix or ".img"
    if ext == ".jpe":
        ext = ".jpg"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{stem}-cover{ext}"
    output.write_bytes(payload)
    issue["cover"]["image_file"] = str(output.resolve())


# ---------------------------------------------------------------------------
# Nature parsing
# ---------------------------------------------------------------------------


def parse_nature(source: Source) -> dict[str, Any]:
    soup = BeautifulSoup(source.html, "html.parser")
    issue_text = clean(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    match = re.search(r"Volume\s+(\d+)\s+Issue\s+(\d+),\s*(.+)", issue_text)
    if not match:
        raise ValueError("Nature volume/issue/date not found")
    volume, issue, date = match.groups()
    toc_url = source.url.split("#", 1)[0]

    cover_box = soup.select_one(".app-volumes-cover__copy")
    if cover_box is None or cover_box.find("h2") is None:
        raise ValueError("Nature cover title/copy not found")
    cover_title = clean(cover_box.find("h2").get_text(" ", strip=True))
    description_node = cover_box.find("p", attrs={"data-promo-text": True})
    description_copy = copy.copy(description_node) if description_node else None
    if description_copy:
        for node in description_copy.select("button, .app-promo-text__emdash"):
            node.decompose()
    cover_description = clean(description_copy.get_text(" ", strip=True) if description_copy else "")
    cover_description = re.sub(r"(?<=\s)0(?=[a-z])", "", cover_description)
    cover_sentences = sentences(cover_description)
    selected = [
        value
        for index, value in enumerate(cover_sentences)
        if index == 0 or " used " in f" {value} " or value.startswith(("They found", "By comparing"))
    ]
    cover_display_description = clean(" ".join(selected))
    credit_node = cover_box.select_one(".app-volumes-cover__image-copy-text")
    cover_credit = clean(credit_node.get_text(" ", strip=True) if credit_node else "")
    cover_article = description_node.select_one('a[href*="doi.org/"]') if description_node else None
    cover_img = soup.select_one(f'img[src*="/journal/41586/{volume}/{issue}"]')
    cover_url = abs_url(source.url, cover_img.get("src") if cover_img else "")

    toc_ids: list[str] = []
    for anchor in soup.select(".app-toc__item a[href]"):
        fragment = urllib.parse.urlparse(anchor.get("href", "")).fragment
        if fragment and fragment not in toc_ids:
            toc_ids.append(fragment)
    if not toc_ids:
        toc_ids = [section.get("id", "") for section in soup.select("section[id]") if section.select_one(".c-card")]
    sections: list[dict[str, Any]] = []
    missing_sections: list[str] = []
    for section_id in toc_ids:
        section = soup.find("section", id=section_id)
        if section is None:
            missing_sections.append(section_id)
            continue
        heading = section.find("h2")
        section_name = clean(heading.get_text(" ", strip=True) if heading else section_id)

        def parse_cards(container: Tag) -> list[dict[str, Any]]:
            """Parse every .c-card inside container in page order."""
            result: list[dict[str, Any]] = []
            for card in container.select(".c-card"):
                title_node = card.select_one(".c-card__title")
                link_node = title_node.select_one("a[href]") if title_node else None
                if title_node is None or link_node is None:
                    continue
                type_node = card.select_one(".c-meta__type")
                summary_node = card.select_one(".c-card__summary")
                url = abs_url(source.url, link_node.get("href"))
                item_type = clean(type_node.get_text(" ", strip=True) if type_node else "")
                summary = clean(summary_node.get_text(" ", strip=True) if summary_node else "")
                result.append(
                    {
                        "id": doi_from(url) or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
                        "type": item_type,
                        "title": clean(title_node.get_text(" ", strip=True)),
                        "summary": summary,
                        "display_summary": bool(summary) and item_type not in NATURE_TITLE_ONLY_TYPES,
                        "authors": _authors(card),
                        "url": url,
                        "doi": doi_from(url),
                    }
                )
            return result

        # Discover in-section groups from the actual TOC DOM: section > ul.app-article-list-row > li,
        # where a li holding h3.c-section-heading--no-bt is a group container and a bare li holds cards
        # directly. Sections without any group container, and bare-card li runs, collapse into one
        # implicit group named after the section itself.
        groups: list[dict[str, Any]] = []
        items_flat: list[dict[str, Any]] = []
        top_ul = section.find("ul", class_="app-article-list-row", recursive=False)
        if top_ul is not None:
            implicit: list[dict[str, Any]] = []
            for li in top_ul.find_all("li", recursive=False):
                group_head = li.find("h3", class_="c-section-heading")
                cards = parse_cards(li)
                if not cards:
                    continue
                if group_head is None:
                    implicit.extend(cards)
                    continue
                for card in implicit:
                    card["group"] = section_name
                if implicit:
                    groups.append({"name": section_name, "items": implicit})
                    items_flat.extend(implicit)
                    implicit = []
                group_name = clean(group_head.get_text(" ", strip=True))
                for card in cards:
                    card["group"] = group_name
                groups.append({"name": group_name, "items": cards})
                items_flat.extend(cards)
            for card in implicit:
                card["group"] = section_name
            if implicit:
                groups.append({"name": section_name, "items": implicit})
                items_flat.extend(implicit)
        if not groups:
            cards = parse_cards(section)
            for card in cards:
                card["group"] = section_name
            if cards:
                groups.append({"name": section_name, "items": cards})
                items_flat = cards
        sections.append(
            {
                "id": section_id,
                "name": section_name,
                "groups": groups,
                "items": items_flat,
            }
        )
    if missing_sections:
        raise ValueError(f"Nature TOC sections missing from page: {', '.join(missing_sections)}")
    if not sections or not any(section["items"] for section in sections):
        raise ValueError("Nature issue contains no TOC card sections")
    return {
        "journal": "Nature",
        "volume": volume,
        "issue": issue,
        "date": date,
        "source_url": toc_url,
        "cover": {
            "title": cover_title,
            "description": cover_description,
            "display_description": cover_display_description,
            "credit": cover_credit,
            "article_url": abs_url(source.url, cover_article.get("href") if cover_article else ""),
            "image_url": cover_url,
        },
        "sections": sections,
        "counts": {
            "sections": len(sections),
            "items": sum(len(section["items"]) for section in sections),
        },
    }


# ---------------------------------------------------------------------------
# Science parsing
# ---------------------------------------------------------------------------


def _science_card(h3: Tag, base_url: str, section_name: str, subsection_name: str) -> dict[str, Any]:
    card = h3.find_parent("div", class_=lambda value: value and "card" in value.split())
    if card is None:
        raise ValueError(f"Science card container not found for {clean(h3.get_text(' ', strip=True))}")
    title_link = h3.select_one("a[href]")
    if title_link is None:
        raise ValueError("Science card title link missing")
    title = clean(h3.get_text(" ", strip=True))
    url = abs_url(base_url, title_link.get("href"))
    meta = card.select_one(".card-meta")
    date_node = meta.select_one("time") if meta else None
    pages = ""
    if meta:
        page_match = re.search(r":\s*([0-9]+(?:[-–][0-9]+)?)", clean(meta.get_text(" ", strip=True)))
        pages = page_match.group(1) if page_match else ""
    # The main card-body is a direct child of .card-content. A bare
    # select_one(".card-body") descends into .card-footer and can match the
    # nested body of a RELATED card (an author line such as "BY ... Science
    # 13 Aug 2026") instead of the card's own abstract. Research Article cards
    # have no main body at all; their abstract sits in the collapsed
    # .card-footer > .collapse > .accordion__content, which the footer-text
    # fallback below extracts.
    abstract_node = card.select_one(".card-content > .card-body")
    if abstract_node is None:
        footer = card.select_one(".card-footer")
        footer_text = clean(footer.get_text(" ", strip=True) if footer else "")
        abstract_match = re.search(r"Abstract\s+(.+?)(?:\s+RELATED\s|$)", footer_text)
        abstract = clean(abstract_match.group(1)) if abstract_match else ""
    else:
        abstract = clean(abstract_node.get_text(" ", strip=True))

    related: list[dict[str, str]] = []
    footer = card.select_one(".card-footer")
    footer_text = clean(footer.get_text(" ", strip=True) if footer else "")
    if footer and "RELATED" in footer_text:
        kind_match = re.search(r"RELATED\s+(Perspective|Research Article|Editorial|Letter)", footer_text, re.I)
        kind = clean(kind_match.group(1)) if kind_match else "Related"
        for anchor in footer.select("a[href]"):
            related_url = abs_url(base_url, anchor.get("href"))
            related_doi = doi_from(related_url)
            related_title = clean(anchor.get_text(" ", strip=True))
            if related_doi and related_title and related_title != "Abstract":
                related.append({"kind": kind, "title": related_title, "url": related_url, "doi": related_doi})
    return {
        "id": doi_from(url) or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
        "section": section_name,
        "subsection": subsection_name,
        "title": title,
        "url": url,
        "doi": doi_from(url),
        "authors": _authors(card),
        "date": clean(date_node.get_text(" ", strip=True) if date_node else ""),
        "pages": pages,
        "abstract": abstract,
        "related": related,
    }


def parse_science_toc(source: Source) -> dict[str, Any]:
    soup = BeautifulSoup(source.html, "html.parser")
    issue_content = soup.select_one(".journal-issue__content")
    issue_text = clean(issue_content.get_text(" ", strip=True) if issue_content else soup.get_text(" ", strip=True))
    match = re.search(r"Science\s+Volume\s+(\d+)\s*\|\s*Issue\s+(\d+)\s*\|\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", issue_text)
    if not match:
        title_text = clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        title_match = re.search(r"Science\s+(\d+),\s*(\d+)", title_text)
        if not title_match:
            raise ValueError("Science volume/issue/date not found")
        volume, issue = title_match.groups()
        date_match = re.search(r"[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}", issue_text)
        date = date_match.group(0) if date_match else ""
    else:
        volume, issue, date = match.groups()
    toc_url = source.url.split("#", 1)[0]

    cover_root = soup.select_one(".journal-issue__cover-image")
    cover_caption = ""
    cover_credit = ""
    cover_url = ""
    if cover_root:
        caption_box = cover_root.select_one(".cover-image__popup-view__caption-wrapper")
        paragraphs = caption_box.find_all("p") if caption_box else []
        if paragraphs:
            cover_caption = clean(paragraphs[0].get_text(" ", strip=True))
            # Strip a leading cover label in any common form (COVER, Cover:, Cover：)
            # so the caption never starts with the cover marker word.
            cover_caption = re.sub(r"^COVER[\s:：]*", "", cover_caption, flags=re.I)
            cover_credit = clean(" ".join(node.get_text(" ", strip=True) for node in paragraphs[1:]))
        cover_img = cover_root.select_one(f'img[src*="science."][src*=".{volume}.issue-{issue}.largecover"]')
        if cover_img is None:
            cover_img = cover_root.select_one('img[src*="largecover"]')
        cover_url = abs_url(source.url, cover_img.get("src") if cover_img else "")
    science_cover_sentences = sentences(cover_caption)
    cover_display_description = clean(
        " ".join(value for value in science_cover_sentences if not value.lower().startswith("see page"))
    )

    sections: list[dict[str, Any]] = []
    for section in soup.select("section.toc__section"):
        heading = section.select_one("h4.to-section")
        if heading is None:
            continue
        section_name = clean(heading.get_text(" ", strip=True))
        subsection_name = ""
        subsection_map: dict[str, list[dict[str, Any]]] = {}
        subsection_order: list[str] = []
        for node in section.find_all(["h5", "h3"]):
            classes = node.get("class") or []
            if node.name == "h5" and "to-section" in classes:
                subsection_name = clean(node.get_text(" ", strip=True))
                continue
            if node.name != "h3" or "article-title" not in classes:
                continue
            if subsection_name not in subsection_map:
                subsection_map[subsection_name] = []
                subsection_order.append(subsection_name)
            item = _science_card(node, source.url, section_name, subsection_name)
            # Digest wrapper recognition is resilient to renamed headings: strip all
            # non-alphanumeric characters before matching the two canonical digest titles.
            item["is_digest_wrapper"] = re.sub(r"[^a-z0-9]", "", item["title"].lower()) in {"insciencejournals", "inotherjournals"}
            subsection_map[subsection_name].append(item)
        sections.append(
            {
                "name": section_name,
                "subsections": [
                    {"name": name, "items": subsection_map[name]} for name in subsection_order
                ],
            }
        )
    if not sections or not any(subsection["items"] for section in sections for subsection in section["subsections"]):
        raise ValueError("Science issue contains no TOC card sections")
    return {
        "journal": "Science",
        "volume": volume,
        "issue": issue,
        "date": date,
        "source_url": toc_url,
        "cover": {"title": "COVER", "description": cover_caption, "display_description": cover_display_description, "credit": cover_credit, "image_url": cover_url},
        "sections": sections,
        "counts": {
            "sections": len(sections),
            "items": sum(len(sub["items"]) for sec in sections for sub in sec["subsections"]),
        },
    }


def parse_science_digest(source: Source, expected_title: str) -> dict[str, Any]:
    soup = BeautifulSoup(source.html, "html.parser")
    h1 = soup.select_one("h1")
    page_title = clean(h1.get_text(" ", strip=True) if h1 else "")
    if page_title != expected_title:
        raise ValueError(f"expected {expected_title!r}, found {page_title!r}")
    items: list[dict[str, Any]] = []
    for section in soup.select("section[id^=sec-]"):
        heading = section.find("h2")
        if heading is None:
            continue
        topic_node = heading.select_one(".core-label")
        topic = clean(topic_node.get_text(" ", strip=True) if topic_node else "")
        full_title = clean(heading.get_text(" ", strip=True))
        headline = clean(full_title[len(topic):]) if topic and full_title.startswith(topic) else full_title
        paragraphs = [clean(node.get_text(" ", strip=True)) for node in section.find_all(attrs={"role": "paragraph"})]
        paragraphs = [value for value in paragraphs if value]
        if len(paragraphs) < 3:
            raise ValueError(f"{expected_title} {section.get('id')}: expected author, intro, citation")
        citation_node = section.find_all(attrs={"role": "paragraph"})[-1]
        doi_links = []
        for anchor in citation_node.find_all("a", href=True):
            doi = doi_from(clean(anchor.get_text(" ", strip=True)) or anchor.get("href", ""))
            if doi:
                doi_links.append({"doi": doi, "url": abs_url(source.url, anchor.get("href"))})
        if not doi_links:
            doi_links = [{"doi": value.rstrip(".)],;"), "url": f"https://doi.org/{value.rstrip('.)],;')}"} for value in DOI_RE.findall(paragraphs[-1])]
        if not doi_links:
            raise ValueError(f"{expected_title} {section.get('id')}: DOI missing")
        items.append(
            {
                "id": doi_links[0]["doi"],
                "topic": topic,
                "headline": headline,
                "title": clean(f"{topic} — {headline}"),
                "author": paragraphs[0],
                "intro": paragraphs[-2],
                "citation": paragraphs[-1],
                "dois": doi_links,
                "url": doi_links[0]["url"],
            }
        )
    if not items:
        raise ValueError(f"no digest items found in {expected_title}")
    return {"name": expected_title, "source_url": source.url.split("#", 1)[0], "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# PPTX building core
# ---------------------------------------------------------------------------


class Relationships:
    def __init__(self, data: bytes):
        self.root = ET.fromstring(data)
        self.by_url: dict[str, str] = {}
        used: list[int] = []
        for node in self.root:
            rid = node.get("Id", "")
            if rid.startswith("rId") and rid[3:].isdigit():
                used.append(int(rid[3:]))
            if node.get("TargetMode") == "External":
                self.by_url[node.get("Target", "")] = rid
        self.next_id = max(used, default=0) + 1

    def hyperlink(self, url: str) -> str:
        if url in self.by_url:
            return self.by_url[url]
        rid = f"rId{self.next_id}"
        self.next_id += 1
        ET.SubElement(
            self.root,
            f"{{{PKG_REL_NS}}}Relationship",
            {
                "Id": rid,
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                "Target": url,
                "TargetMode": "External",
            },
        )
        self.by_url[url] = rid
        return rid

    def bytes(self) -> bytes:
        ET.register_namespace("", PKG_REL_NS)
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)


def chunks(items: list[Any], size: int = 3) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Line-capacity model for list pages.
#
# Most list pages use fixed size tiers selected by page style and item count
# (base_sizes below) and are paginated by an estimated visual-line budget:
# every label line and every English/Chinese title line counts as one visual
# line, wrapping is estimated from CJK full-width vs Latin half-width glyph
# widths, and each RELATED line counts as two lines. Nature Articles is the
# explicit exception: it uses a fixed maximum of two primary items per page so
# long bilingual summaries do not force one-item pages. The planner, set_body(),
# and the structural audit share these rules.
# ---------------------------------------------------------------------------

# Reference body width used by the estimator.  Science list slides are a
# uniform 854 pt wide; Nature role slides vary (753..865), so a mid value keeps
# the estimate stable and conservative enough on all roles.
ESTIMATE_BODY_WIDTH = {"Science": 854.0, "Nature": 828.0}

# Estimated visual lines allowed per list page.  Nature uses the denser budget
# calibrated from the hand-edited reference deck (2 Articles, 2-3 summarised
# items, or 4 title-only items per page); Science keeps the conservative
# 12-line budget.
MAX_PAGE_LINES = {"nature": 20, "science": 12}

# Nature's Articles summaries are long, but the accepted layout is more useful
# at two entries per page. The final page may contain one entry when the issue
# has an odd number of Articles.
NATURE_ARTICLES_PRIMARY_LIMIT = 2


def base_sizes(style: str, count: int) -> dict[str, float]:
    if style == "nature_cover":
        return {"title": 30, "body": 24, "zh": 24, "cite": 14, "gap": 5, "line": 98}
    if style == "science_cover":
        return {"title": 36, "body": 36, "zh": 36, "cite": 14, "gap": 0, "line": 98}
    if style == "list":
        return {"title": 22, "body": 18, "zh": 18.5, "cite": 12, "gap": 4, "line": 94}
    if style == "articles":
        # Nature Articles pages hold at most two items (density rule), which
        # keep the full 24/20/20.5 tier used by the hand-edited reference deck;
        # the compact tier only applies if a page ever exceeds two items.
        return {"title": 21 if count >= 3 else 24, "body": 18 if count >= 3 else 20, "zh": 18.5 if count >= 3 else 20.5, "cite": 13, "gap": 4, "line": 94}
    if style == "research_titles":
        return {"title": 25 if count <= 2 else 23, "body": 19, "zh": 19, "cite": 13, "gap": 10, "line": 98}
    return {"title": 24, "body": 19, "zh": 20, "cite": 13.5, "gap": 5, "line": 98}


def estimated_wrap_lines(text: str, size: float, body_w: float) -> int:
    """Estimated visual lines a single text occupies at a given point size."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    latin = len(text) - cjk
    width = cjk * size + latin * size * 0.5  # CJK ≈ 1em, Latin ≈ 0.5em
    return max(1, math.ceil(width / body_w))


def estimated_title_lines(en: str, zh: str, size: float, body_w: float) -> int:
    return estimated_wrap_lines(en, size, body_w) + estimated_wrap_lines(zh, size, body_w)


def item_visual_lines(item: dict[str, Any], cfg: dict[str, float], body_w: float) -> int:
    """Estimated visual lines for one translated list item (title + summary + related)."""
    lines = estimated_title_lines(item.get("title", ""), item.get("zh", ""), cfg["title"], body_w)
    if item.get("summary"):
        lines += estimated_wrap_lines(item.get("summary", ""), cfg.get("body", cfg["title"]), body_w)
        lines += estimated_wrap_lines(item.get("zh_summary", ""), cfg.get("zh", cfg["title"]), body_w)
    if item.get("related_perspective") or item.get("related_research_article"):
        lines += 2
    return lines


def page_size_config(style: str, count: int, related_count: int) -> dict[str, float]:
    """The exact title/body/related size config set_body() applies for a page."""
    cfg = dict(base_sizes(style, count))
    if style == "research_titles" and related_count >= 2:
        cfg.update(title=18.5, related=14, gap=3, line=94)
    return cfg


def page_visual_lines(page_sections: list[tuple[str, list[dict[str, Any]]]], cfg: dict[str, float], body_w: float) -> int:
    """Estimated visual lines for a page: label lines plus every item's lines."""
    return sum(
        1
        for _label, items in page_sections
        if items and items[0].get("_show_group_label", True)
    ) + sum(
        item_visual_lines(item, cfg, body_w)
        for _label, items in page_sections
        for item in items
    )


def page_fits(page_sections: list[tuple[str, list[dict[str, Any]]]], label: str, item: dict[str, Any], journal: str, section: dict[str, Any] | None = None) -> bool:
    """True if appending (label, item) stays within the applicable page rule."""
    body_w = ESTIMATE_BODY_WIDTH[journal]
    probe = [(probe_label, list(probe_items)) for probe_label, probe_items in page_sections]
    if not probe or probe[-1][0] != label:
        probe.append((label, []))
    probe[-1][1].append(item)

    # Keep the known Nature Articles group on dedicated pages and pair entries
    # mechanically. This deliberately overrides MAX_PAGE_LINES for that group.
    if journal == "Nature":
        candidate_is_articles = NATURE_GROUP_ROLES.get(label) == "articles"
        probe_has_articles = any(
            items and NATURE_GROUP_ROLES.get(probe_label) == "articles"
            for probe_label, items in probe
        )
        if candidate_is_articles:
            if any(
                items and NATURE_GROUP_ROLES.get(probe_label) != "articles"
                for probe_label, items in probe
            ):
                return False
            article_count = sum(
                len(items)
                for probe_label, items in probe
                if NATURE_GROUP_ROLES.get(probe_label) == "articles"
            )
            return article_count <= NATURE_ARTICLES_PRIMARY_LIMIT
        if probe_has_articles:
            return False
        # Hand-edited deck density: a page with summaries holds at most three
        # items, a title-only page at most four.  The line budget below still
        # guards against genuine overflow.
        page_items = [
            probe_item
            for _probe_label, probe_items in probe
            for probe_item in probe_items
        ]
        # Do not mix summarised and title-only items on one page: the hand-edited
        # deck keeps e.g. Comment (summarised) separate from Correspondence
        # (title-only) even though both sit in the same column.
        if page_items and bool(page_items[0].get("summary")) != bool(item.get("summary")):
            return False
        page_has_summary = bool(page_items[0].get("summary")) if page_items else bool(item.get("summary"))
        if len(page_items) > (3 if page_has_summary else 4):
            return False

    style = "research_titles"
    if journal == "Nature" and section is not None:
        style = _nature_page(section, probe)["style"]
    count = sum(len(items) for _label, items in probe)
    related_count = sum(
        bool(probe_item.get("related_perspective") or probe_item.get("related_research_article"))
        for _label, items in probe
        for probe_item in items
    )
    cfg = page_size_config(style, count, related_count)
    return page_visual_lines(probe, cfg, body_w) <= MAX_PAGE_LINES[journal.lower()]


def section(issue: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((value for value in issue["sections"] if value.get("id") == name or value.get("name") == name), None)


def science_subsection(issue: dict[str, Any], section_name: str, subsection_name: str) -> list[dict[str, Any]]:
    sec = next((value for value in issue["sections"] if value["name"] == section_name), None)
    if sec is None:
        return []
    return next((value["items"] for value in sec["subsections"] if value["name"] == subsection_name), [])


def science_section_items(issue: dict[str, Any], section_name: str) -> list[dict[str, Any]]:
    sec = next((value for value in issue["sections"] if value["name"] == section_name), None)
    if sec is None:
        return []
    return [item for subsection in sec["subsections"] for item in subsection["items"] if not item.get("is_digest_wrapper")]


def date_slug(value: str) -> str:
    for pattern in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-")


def translated_item(source: dict[str, Any], translations: dict[str, Any], show_summary: bool) -> dict[str, Any]:
    translated = translations[source["id"]]
    # Science TOC cards store the abstract excerpt under "abstract" (Nature
    # uses "summary"); both feed the same bilingual summary line. A card with
    # no excerpt simply renders its title only.
    summary = (source.get("summary") or source.get("abstract", "")) if show_summary else ""
    return {
        "id": source["id"],
        "title": source["title"],
        "zh": translated["chinese_title"],
        "summary": summary,
        "zh_summary": translated.get("chinese_summary", "") if summary else "",
        "url": source.get("url", ""),
    }


def validate_translations(issue: dict[str, Any], translations: dict[str, Any]) -> None:
    missing: list[str] = []
    for journal in ("nature", "science"):
        if journal not in translations:
            continue
        cover = translations[journal]["cover"]
        for field in ("chinese_title", "chinese_summary"):
            if cover.get("english_" + field.split("chinese_", 1)[1]) and not cover.get(field):
                missing.append(f"{journal}.cover.{field}")
        for item_id, item in translations[journal]["items"].items():
            if not item.get("chinese_title"):
                missing.append(f"{journal}.items.{item_id}.chinese_title")
            if journal == "nature" and item.get("english_summary") and not item.get("chinese_summary"):
                missing.append(f"nature.items.{item_id}.chinese_summary")
    digests = translations.get("science", {}).get("digests", {})
    for digest_name, digest in digests.items():
        for item_id, item in digest.items():
            for field in ("chinese_topic", "chinese_headline", "chinese_intro"):
                if not item.get(field):
                    missing.append(f"science.digests.{digest_name}.{item_id}.{field}")
    if missing:
        preview = "\n".join(missing[:30])
        raise ValueError(f"translation worksheet incomplete ({len(missing)} fields):\n{preview}")


def rpr(parent: ET.Element, run: dict[str, Any], rels: Relationships) -> ET.Element:
    attrs = {"lang": run.get("lang", "en-US"), "sz": str(int(run["size"] * 100)), "dirty": "0"}
    if run.get("bold"):
        attrs["b"] = "1"
    if run.get("underline"):
        attrs["u"] = "sng"
    if run.get("caps"):
        attrs["cap"] = "all"
    node = ET.SubElement(parent, A + "rPr", attrs)
    fill = ET.SubElement(node, A + "solidFill")
    ET.SubElement(fill, A + "srgbClr", {"val": run.get("color", WHITE)})
    ET.SubElement(node, A + "latin", {"typeface": "Calibri"})
    ET.SubElement(node, A + "ea", {"typeface": "宋体"})
    if run.get("url"):
        ET.SubElement(node, A + "hlinkClick", {R + "id": rels.hyperlink(run["url"]), "tooltip": run.get("tooltip", "Open source article")})
    return node


def add_paragraph(txbody: ET.Element, runs: list[dict[str, Any]], rels: Relationships, *, size: float, bullet: bool = False, level: int = 0, before: float = 0, after: float = 1, line: float = 96, inherit_level: bool = False, right_tab_pos: int = 0) -> ET.Element:
    paragraph = ET.SubElement(txbody, A + "p")
    ppr = ET.SubElement(paragraph, A + "pPr", {"lvl": str(level)})
    if not inherit_level:
        ppr.set("marL", "228600" if bullet else "0")
        ppr.set("indent", "-228600" if bullet else "0")
        ln = ET.SubElement(ppr, A + "lnSpc")
        ET.SubElement(ln, A + "spcPct", {"val": str(int(line * 1000))})
        if before:
            box = ET.SubElement(ppr, A + "spcBef")
            ET.SubElement(box, A + "spcPts", {"val": str(int(before * 100))})
        box = ET.SubElement(ppr, A + "spcAft")
        ET.SubElement(box, A + "spcPts", {"val": str(int(after * 100))})
        ET.SubElement(ppr, A + ("buChar" if bullet else "buNone"), {"char": "•"} if bullet else {})
    if right_tab_pos:
        tabs = ET.SubElement(ppr, A + "tabLst")
        ET.SubElement(tabs, A + "tab", {"pos": str(right_tab_pos), "algn": "r"})
    for run in runs:
        if run.get("break"):
            ET.SubElement(paragraph, A + "br")
            continue
        if not run.get("text"):
            continue
        rr = ET.SubElement(paragraph, A + "r")
        rpr(rr, run, rels)
        ET.SubElement(rr, A + "t").text = run["text"]
    ET.SubElement(paragraph, A + "endParaRPr", {"lang": "en-US", "sz": str(int(size * 100))})
    return paragraph


def clear_txbody(txbody: ET.Element) -> None:
    keep = [node for node in list(txbody) if node.tag in {A + "bodyPr", A + "lstStyle"}]
    for node in list(txbody):
        txbody.remove(node)
    for node in keep:
        txbody.append(node)


def header_size(journal: str, category: str, meta: str = "", title_width_emu: int = 0) -> int:
    if journal == "Science":
        tiers = ((34, 66), (30, 75), (26, 87))
        base = tiers[0][0] if len(category) <= 18 else tiers[1][0] if len(category) <= 28 else tiers[2][0]
    else:
        if category in {"Amendments & Corrections", "Technology Feature"}:
            base = 32
        elif category == "NEWS":
            base = 36
        else:
            base = 42 if len(category) <= 18 else 36 if len(category) <= 28 else 32
        tiers = ((42, 54), (36, 63), (32, 71), (28, 81))
    # The volume metadata shares one header line with the journal + column name
    # through a right-aligned tab stop.  If the combined text would exceed the
    # tab position, drop to a smaller tier so the metadata never wraps onto a
    # second line (e.g. "News in Focus" totals 57 chars and overflows at 42 pt;
    # 36 pt fits, as in the hand-edited reference deck, while "This Week" at
    # 54 chars still fits at 42 pt).  Character caps are calibrated from that
    # deck: 42 pt holds ~54 combined chars, 36 pt ~63, 32 pt ~71, 28 pt ~81.
    display_journal = "" if journal == "Science" and category in {"In Science Journals", "In Other Journals"} else journal
    total = len(display_journal) + (1 if display_journal else 0) + len(category) + len(meta)
    if title_width_emu and meta and total > tiers[0][1]:
        for tier, cap in tiers:
            if total <= cap:
                return min(base, tier)
        return tiers[-1][0]
    return base


def set_header(txbody: ET.Element, journal: str, category: str, meta: str, rels: Relationships, title_width_emu: int = 0) -> None:
    clear_txbody(txbody)
    size = header_size(journal, category, meta, title_width_emu)
    display_journal = "" if journal == "Science" and category in {"In Science Journals", "In Other Journals"} else journal
    runs: list[dict[str, Any]] = [{"text": display_journal, "size": size, "bold": True, "color": WHITE}]
    if category:
        runs.extend([
            {"text": (" " if display_journal else "") + category, "size": size, "color": YELLOW},
        ])
    # Right-aligned tab: the volume metadata hugs the right edge of the title
    # placeholder regardless of the column name length.  A 0.5 cm right inset
    # keeps it off the very edge; digest headers that already carry the issue
    # line in the body keep the journal-less left label only.
    if title_width_emu and meta:
        # Tab character in text + right-aligned tab stop in pPr: volume metadata
        # hugs the right edge of the title placeholder regardless of column length.
        # a:tabLst pos is measured from the paragraph left edge (text-area left,
        # i.e. after the default 0.1" inset); subtract both insets plus a small
        # right breathing room so the text sits just inside the placeholder edge.
        runs.append({"text": "\t", "size": size, "bold": True, "color": WHITE})
        right_tab_pos = max(0, title_width_emu - 2 * 91440 - 50000)
    else:
        right_tab_pos = 0
    runs.append({"text": meta, "size": size, "bold": True, "color": WHITE})
    add_paragraph(txbody, runs, rels, size=size, after=0, line=90, right_tab_pos=right_tab_pos)


def append_article(txbody: ET.Element, item: dict[str, Any], cfg: dict[str, float], rels: Relationships, bullet_title: bool) -> None:
    title_runs = [
        {"text": item["title"], "size": cfg["title"], "bold": True, "underline": True, "color": YELLOW, "url": item.get("url")},
        {"break": True},
        {"text": item["zh"], "size": cfg["title"], "bold": True, "underline": True, "color": YELLOW, "lang": "zh-CN", "url": item.get("url")},
    ]
    add_paragraph(txbody, title_runs, rels, size=cfg["title"], bullet=bullet_title, before=cfg["gap"], after=0.5, line=cfg["line"])
    # Bilingual summary first, then the subordinate RELATED lines: the item's
    # own abstract excerpt precedes the related Perspective/Research Article.
    if item.get("summary"):
        add_paragraph(
            txbody,
            [
                {"text": item["summary"], "size": cfg["body"], "color": WHITE},
                {"break": True},
                {"text": item["zh_summary"], "size": cfg["zh"], "color": WHITE, "lang": "zh-CN"},
            ],
            rels,
            size=cfg["body"],
            bullet=True,
            after=0.4,
            line=cfg["line"],
        )
    related = item.get("related_perspective")
    if related:
        size = cfg.get("related", max(16, cfg["title"] - 6))
        add_paragraph(
            txbody,
            [
                {"text": "RELATED PERSPECTIVE  ", "size": size, "bold": True, "caps": True, "color": WHITE},
                {"text": related["title"], "size": size, "bold": True, "underline": True, "color": YELLOW, "url": related["url"], "tooltip": "Open related Perspective"},
                {"break": True},
                {"text": related["zh"], "size": size, "bold": True, "underline": True, "color": YELLOW, "lang": "zh-CN", "url": related["url"], "tooltip": "Open related Perspective"},
            ],
            rels,
            size=size,
            level=1,
            inherit_level=True,
        )
    related = item.get("related_research_article")
    if related:
        size = cfg.get("related", max(16, cfg["title"] - 6))
        add_paragraph(
            txbody,
            [
                {"text": "RELATED RESEARCH ARTICLE  ", "size": size, "bold": True, "caps": True, "color": WHITE},
                {"text": related["title"], "size": size, "bold": True, "underline": True, "color": YELLOW, "url": related["url"], "tooltip": "Open related Research Article"},
                {"break": True},
                {"text": related["zh"], "size": size, "bold": True, "underline": True, "color": YELLOW, "lang": "zh-CN", "url": related["url"], "tooltip": "Open related Research Article"},
            ],
            rels,
            size=size,
            level=1,
            inherit_level=True,
        )


def digest_sizes(item: dict[str, Any]) -> dict[str, float]:
    total = len(item["topic"] + item["zh_topic"] + item["headline"] + item["zh_headline"] + item["summary"] + item["zh_summary"] + item["cite"])
    if total < 700:
        return {"title": 32, "en": 24, "zh": 24, "cite": 20, "scale": 100000, "reduction": 10000}
    if total <= 1100:
        return {"title": 24, "en": 24, "zh": 24, "cite": 20, "scale": 92500, "reduction": 10000}
    if total <= 1400:
        # Full bilingual digest copy above roughly 1,100 characters can wrap far
        # more than its raw character count suggests (long Latin words and mixed
        # CJK/Latin runs are the usual trigger).  Use the compact tier early
        # enough to keep one digest item on one slide without bottom clipping.
        return {"title": 20, "en": 18, "zh": 20, "cite": 16, "scale": 100000, "reduction": 15000}
    return {"title": 18, "en": 16.5, "zh": 18, "cite": 15, "scale": 100000, "reduction": 18000}


def append_digest(txbody: ET.Element, item: dict[str, Any], rels: Relationships) -> None:
    cfg = digest_sizes(item)
    add_paragraph(
        txbody,
        [
            {"text": item["topic"] + "\u00a0\u00a0", "size": cfg["title"], "bold": True, "caps": True, "color": WHITE},
            {"text": item["zh_topic"] + "\u00a0\u00a0", "size": cfg["title"], "bold": True, "color": WHITE, "lang": "zh-CN"},
            {"text": item["headline"] + "\u00a0\u00a0", "size": cfg["title"], "bold": True, "color": WHITE},
            {"text": item["zh_headline"], "size": cfg["title"], "bold": True, "color": WHITE, "lang": "zh-CN"},
        ],
        rels,
        size=cfg["title"],
        after=0,
        line=100,
    )
    add_paragraph(
        txbody,
        [
            {"text": item["summary"], "size": cfg["en"], "color": WHITE},
            {"break": True},
            {"text": item["zh_summary"], "size": cfg["zh"], "color": WHITE, "lang": "zh-CN"},
        ],
        rels,
        size=cfg["en"],
        bullet=True,
        after=0.4,
        line=100,
    )
    runs: list[dict[str, Any]] = []
    cursor = 0
    doi_urls = {entry["doi"]: entry["url"] for entry in item["dois"]}
    for match in DOI_RE.finditer(item["cite"]):
        value = match.group(0).rstrip(".)],;")
        if match.start() > cursor:
            runs.append({"text": item["cite"][cursor:match.start()], "size": cfg["cite"], "color": WHITE})
        runs.append({"text": value, "size": cfg["cite"], "color": YELLOW, "underline": True, "url": doi_urls.get(value, f"https://doi.org/{value}")})
        cursor = match.start() + len(value)
    if cursor < len(item["cite"]):
        runs.append({"text": item["cite"][cursor:], "size": cfg["cite"], "color": WHITE})
    add_paragraph(txbody, runs, rels, size=cfg["cite"], level=1, inherit_level=True)
    bodypr = txbody.find(A + "bodyPr")
    if bodypr is not None:
        for node in list(bodypr):
            if node.tag in {A + "normAutofit", A + "spAutoFit", A + "noAutofit"}:
                bodypr.remove(node)
        ET.SubElement(bodypr, A + "normAutofit", {"fontScale": str(int(cfg["scale"])), "lnSpcReduction": str(int(cfg["reduction"]))})


def science_cover_sizes(item: dict[str, Any]) -> dict[str, Any]:
    """Size the Science cover copy by total character count so the bilingual
    description fills the body placeholder without overflowing. PowerPoint does
    not recompute a bare normAutofit on load, so the scale is written explicitly."""
    total = len(item["summary"]) + len(item["zh_summary"])
    if total <= 200:
        return {"size": 34, "scale": 100000, "reduction": 5000}
    if total <= 400:
        return {"size": 30, "scale": 100000, "reduction": 10000}
    if total <= 650:
        return {"size": 26, "scale": 95000, "reduction": 10000}
    if total <= 900:
        return {"size": 22, "scale": 95000, "reduction": 10000}
    return {"size": 20, "scale": 90000, "reduction": 15000}


def nature_cover_sizes(item: dict[str, Any]) -> dict[str, Any]:
    """Size the Nature cover copy by estimated bilingual line budget so the
    complete English description and its full Chinese translation fit the body
    placeholder (565 x 470 pt, title band above).  The estimate uses equivalent
    half-width units: Latin glyphs ~0.9 (space included), CJK glyphs = 2.0."""
    width = int(len(item["summary"]) * 0.9 + len(item["zh_summary"]) * 2.0)
    if width <= 300:
        return {"size": 24, "scale": 100000, "reduction": 5000}
    if width <= 600:
        return {"size": 20, "scale": 97000, "reduction": 10000}
    if width <= 1000:
        return {"size": 17, "scale": 94000, "reduction": 12000}
    if width <= 1600:
        return {"size": 15, "scale": 91000, "reduction": 14000}
    return {"size": 14, "scale": 88000, "reduction": 16000}


def set_body(txbody: ET.Element, spec: dict[str, Any], rels: Relationships) -> None:
    clear_txbody(txbody)
    # Ordinary list pages must not inherit the template's stale normAutofit
    # fontScale (e.g. 77500 → text rendered at 77.5%).  Reset the placeholder
    # to a bare normAutofit so PowerPoint shows the written point sizes at 100%
    # and only shrinks dynamically if content genuinely overflows.  Cover and
    # digest slides manage their own autofit below / in append_digest().
    if spec["style"] not in {"science_cover", "nature_cover", "review"}:
        bodypr = txbody.find(A + "bodyPr")
        if bodypr is not None:
            for node in list(bodypr):
                if node.tag in {A + "normAutofit", A + "spAutoFit", A + "noAutofit"}:
                    bodypr.remove(node)
            ET.SubElement(bodypr, A + "normAutofit")
    items = spec.get("items", [])
    section_items = [item for _label, group_items in spec.get("sections", []) for item in group_items]
    if spec["style"] == "science_cover":
        item = items[0]
        cfg = science_cover_sizes(item)
        add_paragraph(
            txbody,
            [
                {"text": item["summary"], "size": cfg["size"], "color": WHITE},
                {"break": True},
                {"text": item["zh_summary"], "size": cfg["size"], "color": WHITE, "lang": "zh-CN"},
            ],
            rels,
            size=cfg["size"],
            line=98,
        )
        bodypr = txbody.find(A + "bodyPr")
        if bodypr is not None:
            for node in list(bodypr):
                if node.tag in {A + "normAutofit", A + "spAutoFit", A + "noAutofit"}:
                    bodypr.remove(node)
            ET.SubElement(bodypr, A + "normAutofit", {"fontScale": str(cfg["scale"]), "lnSpcReduction": str(cfg["reduction"])})
        return
    if spec["style"] == "nature_cover":
        item = items[0]
        cfg = nature_cover_sizes(item)
        add_paragraph(
            txbody,
            [
                {"text": item["title"], "size": 30, "bold": True, "color": WHITE},
                {"text": item["zh"], "size": 30, "bold": True, "color": WHITE, "lang": "zh-CN"},
            ],
            rels,
            size=30,
            after=1,
        )
        add_paragraph(
            txbody,
            [
                {"text": item["summary"], "size": cfg["size"], "color": WHITE},
                {"break": True},
                {"text": item["zh_summary"], "size": cfg["size"], "color": WHITE, "lang": "zh-CN"},
            ],
            rels,
            size=cfg["size"],
            line=98,
        )
        bodypr = txbody.find(A + "bodyPr")
        if bodypr is not None:
            for node in list(bodypr):
                if node.tag in {A + "normAutofit", A + "spAutoFit", A + "noAutofit"}:
                    bodypr.remove(node)
            ET.SubElement(bodypr, A + "normAutofit", {"fontScale": str(cfg["scale"]), "lnSpcReduction": str(cfg["reduction"])})
        return
    # Ordinary list pages: fixed size config by style and item count, with the
    # related-line shrink rule for dense research pages (same as the planner).
    count = len(items) or sum(len(value[1]) for value in spec.get("sections", []))
    cfg = base_sizes(spec["style"], count)
    related_count = sum(bool(item.get("related_perspective") or item.get("related_research_article")) for item in section_items)
    if (spec.get("category") == "Res Articles" and related_count >= 2) or (
        spec["style"] == "research_titles" and related_count >= 2
    ):
        cfg = dict(cfg)
        cfg.update(title=18.5, related=14, gap=3, line=94)
    if spec.get("sections"):
        for heading, section_items in spec["sections"]:
            if section_items and section_items[0].get("_show_group_label", True):
                add_paragraph(txbody, [{"text": heading, "size": cfg["title"] + 1, "bold": True, "color": WHITE}], rels, size=cfg["title"] + 1, before=3, after=1, line=92)
            for item in section_items:
                append_article(txbody, item, cfg, rels, False)
        return
    for item in items:
        if spec["style"] == "review":
            append_digest(txbody, item, rels)
        else:
            append_article(txbody, item, cfg, rels, spec["style"] == "research_titles")


def find_placeholders(root: ET.Element) -> tuple[ET.Element, ET.Element, int]:
    """Return (title_txbody, body_txbody, title_width_emu) from the template slide."""
    title = body = None
    title_width = 0
    for shape in root.findall(".//p:sp", NS):
        ph = shape.find("./p:nvSpPr/p:nvPr/p:ph", NS)
        txbody = shape.find("./p:txBody", NS)
        if ph is None or txbody is None:
            continue
        if ph.get("type") in {"title", "ctrTitle"}:
            title = txbody
            xfrm = shape.find("./p:spPr/a:xfrm", NS)
            ext = xfrm.find("a:ext", NS) if xfrm is not None else None
            title_width = int(ext.get("cx", 0)) if ext is not None else 0
        elif ph.get("type") in {"body", "obj", "subTitle"} or ph.get("idx") == "1":
            body = txbody
    if title is None or body is None:
        raise ValueError("template slide lacks title/body placeholders")
    return title, body, title_width


NATURE_GROUP_ROLES = {
    "Editorial": "this_week",
    "World View": "this_week",
    "Research Highlights": "research_highlight",
    "Research Highlight": "research_highlight",
    "News": "news_list",
    "News Q&A": "news_feature",
    "Features": "news_feature",
    "News Feature": "news_feature",
    "Book Review": "books_comment_correspondence_work",
    "Obituary": "books_comment_correspondence_work",
    "Comment": "books_comment_correspondence_work",
    "Correspondence": "books_comment_correspondence_work",
    "Feature": "books_comment_correspondence_work",
    "Career Feature": "books_comment_correspondence_work",
    "News & Views": "news_and_views",
    "Articles": "articles",
    "Article": "articles",
    "Amendments & Corrections": "articles_corrections_technology",
    "Technology Feature": "articles_corrections_technology",
}
NATURE_ROLE_SLIDES = {
    "this_week": 2,
    "research_highlight": 3,
    "news_list": 4,
    "news_feature": 5,
    "books_comment_correspondence_work": 6,
    "news_and_views": 7,
    "articles": 8,
    "articles_corrections_technology": 8,
}
# Template-level overrides for the slide title. By default the title is the section (column) name
# read from the page; an entry here forces the title to the group name instead.
NATURE_TITLE_BY_ROLE: dict[str, bool] = {}

SCIENCE_SUBSECTION_ROLES = {
    "In Depth": "news",
    "Feature": "news",
    "Expert Voices": "commentary",
    "Perspectives": "commentary",
    "Policy Forum": "commentary",
    "Books et al.": "commentary",
    "Letters": "commentary",
    "Research Highlights": "research",
    "Research Articles": "research",
    "Errata": "research",
    "expression of concern": "research",
    "Working Life": "working_life",
}
SCIENCE_SECTION_ROLES = {
    "Editorial": "editorial",
    "News": "news",
    "Commentary": "commentary",
    "Essays": "essays",
    "Research": "research",
    "Reviews": "research",
    "Careers": "working_life",
}
# Science TOC cards carry an abstract excerpt that renders as a bilingual
# summary line under each item title (user-approved enrichment). Cards
# without an excerpt simply render their title only. Research Article cards
# are excluded: their TOC excerpt is a truncated abstract teaser ("..."), and
# the complete introduction is carried by the In Science Journals digest.
SCIENCE_SUMMARY_EXCLUDE_SUBSECTIONS = {"Research Articles"}
SCIENCE_ROLE_SLIDES = {
    "editorial": 2,
    "news": 4,
    "commentary": 3,
    "essays": 3,
    "research": 4,
    "working_life": 3,
    "in_science_journals": 9,
    "in_other_journals": 13,
}


def _group_role(label: str, items: list[dict[str, Any]]) -> str:
    role = NATURE_GROUP_ROLES.get(label)
    if role:
        return role
    counts: dict[str, int] = {}
    for item in items:
        value = item.get("type") or ""
        counts[value] = counts.get(value, 0) + 1
    majority = max(counts, key=counts.get) if counts else ""
    return NATURE_GROUP_ROLES.get(majority, "")


def _role_style(role: str, show_summary: bool) -> str:
    if not show_summary:
        return "research_titles"
    return {"news_list": "list", "news_and_views": "list", "articles": "articles"}.get(role, "standard")


def _nature_page(section: dict[str, Any], sections: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    label, items = max(sections, key=lambda pair: len(pair[1]))
    role = _group_role(label, items)
    show_summary = any(item.get("summary") for item in items)
    if NATURE_TITLE_BY_ROLE.get(role):
        title = label
    else:
        title = section["name"]
    return {
        "title": title,
        "template": role or "standard",
        "style": _role_style(role, show_summary),
        "sections": sections,
    }


def plan_nature(issue: dict[str, Any], translations: dict[str, Any]) -> list[dict[str, Any]]:
    titems = translations["items"]
    pages: list[dict[str, Any]] = [
        {
            "category": "",
            "style": "nature_cover",
            "items": [{
                "title": translations["cover"]["english_title"],
                "zh": translations["cover"]["chinese_title"],
                "summary": translations["cover"]["english_summary"],
                "zh_summary": translations["cover"]["chinese_summary"],
            }],
        }
    ]
    for source_section in issue["sections"]:
        groups = source_section.get("groups") or (
            [{"name": source_section["name"], "items": source_section["items"]}] if source_section.get("items") else []
        )
        entries: list[tuple[str, dict[str, Any]]] = [
            (group["name"], item)
            for group in groups
            for item in group["items"]
        ]
        if not entries:
            continue
        # Column-aggregated mixing: fill pages in group order and split when the
        # applicable capacity rule is reached. Each group label appears only
        # before that group's first item in the column, never on continuation pages.
        page_sections: list[tuple[str, list[dict[str, Any]]]] = []
        shown_group_labels: set[str] = set()
        for group_name, item in entries:
            translated = translated_item(item, titems, item.get("display_summary", False))
            translated["_show_group_label"] = group_name not in shown_group_labels
            if not page_fits(page_sections, group_name, translated, "Nature", source_section):
                pages.append(_nature_page(source_section, page_sections))
                page_sections = []
            if not page_sections or page_sections[-1][0] != group_name:
                page_sections.append((group_name, []))
            page_sections[-1][1].append(translated)
            shown_group_labels.add(group_name)
        if page_sections:
            pages.append(_nature_page(source_section, page_sections))
    return pages


def _science_role(section_name: str, subsection_name: str) -> str:
    return SCIENCE_SUBSECTION_ROLES.get(subsection_name) or SCIENCE_SECTION_ROLES.get(section_name) or ""


def _science_page(section: dict[str, Any], sections: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    label, _items = max(sections, key=lambda pair: len(pair[1]))
    role = _science_role(section["name"], label)
    return {
        "title": section["name"],
        "template": role or "standard",
        "style": "research_titles",
        "sections": sections,
    }


def plan_science(issue: dict[str, Any], translations: dict[str, Any]) -> list[dict[str, Any]]:
    titems = translations["items"]
    pages: list[dict[str, Any]] = [
        {
            "category": "",
            "style": "science_cover",
            "items": [{
                "title": "COVER",
                "zh": translations["cover"]["chinese_title"],
                "summary": translations["cover"]["english_summary"],
                "zh_summary": translations["cover"]["chinese_summary"],
            }],
        }
    ]
    for source_section in issue["sections"]:
        entries: list[tuple[str, dict[str, Any]]] = []
        for subsection in source_section["subsections"]:
            label = subsection.get("name") or source_section["name"]
            for item in subsection["items"]:
                if item.get("is_digest_wrapper"):
                    continue
                entries.append((label, item))
        if not entries:
            continue
        # Column-driven mixing: pages fill in subsection order and split when the
        # visual-line budget is reached. Each subsection label appears only before
        # its first item in the column, never on continuation pages.
        page_sections: list[tuple[str, list[dict[str, Any]]]] = []
        shown_subsection_labels: set[str] = set()
        for label, item in entries:
            translated = translated_item(
                item, titems, item.get("subsection", "") not in SCIENCE_SUMMARY_EXCLUDE_SUBSECTIONS
            )
            translated["_show_group_label"] = label not in shown_subsection_labels
            perspectives = [value for value in item.get("related", []) if value["kind"].lower() == "perspective"]
            if perspectives:
                related = perspectives[0]
                related_translation = titems.get(related["doi"], {})
                translated["related_perspective"] = {
                    "title": related["title"],
                    "zh": related_translation.get("chinese_title", ""),
                    "url": related["url"],
                }
            research_articles = [value for value in item.get("related", []) if value["kind"].lower() == "research article"]
            if research_articles:
                related = research_articles[0]
                related_translation = titems.get(related["doi"], {})
                translated["related_research_article"] = {
                    "title": related["title"],
                    "zh": related_translation.get("chinese_title", ""),
                    "url": related["url"],
                }
            if not page_fits(page_sections, label, translated, "Science", source_section):
                pages.append(_science_page(source_section, page_sections))
                page_sections = []
            if not page_sections or page_sections[-1][0] != label:
                page_sections.append((label, []))
            page_sections[-1][1].append(translated)
            shown_subsection_labels.add(label)
        if page_sections:
            pages.append(_science_page(source_section, page_sections))

    for digest_key, category in (("in_science_journals", "In Science Journals"), ("in_other_journals", "In Other Journals")):
        dtrans = translations["digests"][digest_key]
        for source in issue["digests"][digest_key]["items"]:
            translated = dtrans[source["id"]]
            cite, dois = first_doi_citation(source["citation"], source["dois"])
            pages.append(
                {
                    "category": category,
                    "style": "review",
                    "items": [{
                        "topic": source["topic"],
                        "zh_topic": translated["chinese_topic"],
                        "headline": source["headline"],
                        "zh_headline": translated["chinese_headline"],
                        "summary": source["intro"],
                        "zh_summary": translated["chinese_intro"],
                        "cite": cite,
                        "dois": dois,
                    }],
                }
            )
    return pages


def first_doi_citation(citation: str, dois: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Trim a digest citation to its first DOI entry only.

    Digest citations occasionally carry a second DOI ("see also p. ..., DOI"
    or a second co-submitted journal article).  The deck shows only the first
    citation entry, so text after the first DOI is dropped and only that DOI
    (and its URL) is kept.
    """
    first = DOI_RE.search(citation)
    if first is None:
        return citation, dois
    end = first.end()
    return citation[:end], [dois[0]] if dois else []


def template_slide(journal: str, spec: dict[str, Any]) -> int:
    category = spec.get("category", "")
    if spec["style"] in {"nature_cover", "science_cover"}:
        return 1
    if journal == "Nature":
        role = spec.get("template")
        if role:
            return NATURE_ROLE_SLIDES.get(role, 2)
        return {
            "Research Highlight": 3,
            "NEWS": 4 if spec["style"] == "list" else 5,
            "Comment": 6,
            "Books & Arts": 6,
            "Correspondence": 6,
            "Work": 6,
            "News & Views": 7,
            "Articles": 8,
            "Amendments & Corrections": 8,
            "Technology Feature": 8,
        }.get(category, 2)
    if journal == "Science":
        role = spec.get("template")
        if role:
            return SCIENCE_ROLE_SLIDES.get(role, 2)
        return {
            "Commentary": 3,
            "Essays": 3,
            "Working Life": 3,
            "NEWS": 4,
            "Res Articles": 4,
            "In Science Journals": 9,
            "In Other Journals": 13,
        }.get(category, 2)
    return 2


def prepare_sequence(package: dict[str, bytes], source_indices: list[int]) -> None:
    original = dict(package)
    original_count = len([name for name in package if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)])
    for dest, source in enumerate(source_indices, 1):
        package[f"ppt/slides/slide{dest}.xml"] = original[f"ppt/slides/slide{source}.xml"]
        rel_source = f"ppt/slides/_rels/slide{source}.xml.rels"
        if rel_source in original:
            package[f"ppt/slides/_rels/slide{dest}.xml.rels"] = original[rel_source]
    if len(source_indices) <= original_count:
        return
    # The template may hold fewer slides than the deck needs, or hold them at
    # different numbers than the role mapping expects.  Rebuild the sldIdLst and
    # the slide relationships from scratch over the output slide numbers, so a
    # slimmed template (kept slides at their original numbers) produces the same
    # correct deck as a full template.  Namespace prefixes are re-registered so
    # the rewritten XML keeps PowerPoint-compatible r:id attributes.
    pres = ET.fromstring(package["ppt/presentation.xml"])
    pres_rels = ET.fromstring(package["ppt/_rels/presentation.xml.rels"])
    slide_ids = pres.find("p:sldIdLst", NS)
    used_rids = [int(node.get("Id")[3:]) for node in pres_rels if node.get("Id", "")[3:].isdigit()]
    next_rid = max(used_rids) + 1
    next_sid = max(int(node.get("id")) for node in slide_ids) + 1
    # remove template sldId entries and slide rels; they will be re-added for
    # every output slide, keyed to the output slide numbers.
    for node in list(slide_ids):
        slide_ids.remove(node)
    for node in list(pres_rels):
        if node.get("Type", "").endswith("/relationships/slide"):
            pres_rels.remove(node)
    for index in range(1, len(source_indices) + 1):
        rid = f"rId{next_rid}"
        next_rid += 1
        ET.SubElement(pres_rels, f"{{{PKG_REL_NS}}}Relationship", {"Id": rid, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", "Target": f"slides/slide{index}.xml"})
        ET.SubElement(slide_ids, P + "sldId", {"id": str(next_sid), R + "id": rid})
        next_sid += 1
    package["ppt/presentation.xml"] = ET.tostring(pres, encoding="utf-8", xml_declaration=True)
    ET.register_namespace("", PKG_REL_NS)
    package["ppt/_rels/presentation.xml.rels"] = ET.tostring(pres_rels, encoding="utf-8", xml_declaration=True)
    content = ET.fromstring(package["[Content_Types].xml"])
    existing = {node.get("PartName") for node in content}
    for index in range(1, len(source_indices) + 1):
        part = f"/ppt/slides/slide{index}.xml"
        if part not in existing:
            ET.SubElement(content, f"{{{CONTENT_TYPES_NS}}}Override", {"PartName": part, "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"})
    ET.register_namespace("", CONTENT_TYPES_NS)
    package["[Content_Types].xml"] = ET.tostring(content, encoding="utf-8", xml_declaration=True)


def png_bytes(path: Path) -> bytes:
    with Image.open(path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def build(template: Path, output: Path, journal: str, issue: dict[str, Any], pages: list[dict[str, Any]], cover: Path) -> None:
    with zipfile.ZipFile(template) as archive:
        package = {name: archive.read(name) for name in archive.namelist()}
    prepare_sequence(package, [template_slide(journal, page) for page in pages])
    meta = f"Volume {issue['volume']} Issue {issue['issue']}, {issue['date']}" if journal == "Nature" else f"Volume {issue['volume']} | Issue {issue['issue']} | {issue['date']}"
    for index, spec in enumerate(pages, 1):
        slide_name = f"ppt/slides/slide{index}.xml"
        rels_name = f"ppt/slides/_rels/slide{index}.xml.rels"
        root = ET.fromstring(package[slide_name])
        rels = Relationships(package[rels_name])
        tree = root.find(".//p:spTree", NS)
        for shape in list(tree.findall("p:sp", NS)):
            ph = shape.find("./p:nvSpPr/p:nvPr/p:ph", NS)
            if ph is None and shape.find(".//a:t", NS) is not None:
                tree.remove(shape)
        title, body, title_width_emu = find_placeholders(root)
        set_header(title, journal, spec.get("title", spec.get("category", "")), meta, rels, title_width_emu)
        set_body(body, spec, rels)
        for picture in root.findall(".//p:pic", NS):
            props = picture.find("./p:nvPicPr/p:cNvPr", NS)
            if props is not None:
                for old in list(props.findall("a:hlinkClick", NS)):
                    props.remove(old)
                ET.SubElement(props, A + "hlinkClick", {R + "id": rels.hyperlink(issue["source_url"]), "tooltip": "Open issue contents"})
        package[slide_name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        package[rels_name] = rels.bytes()
    package["ppt/media/image1.png"] = png_bytes(cover)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in package.items():
            archive.writestr(name, payload)


# ---------------------------------------------------------------------------
# Structural audit
# ---------------------------------------------------------------------------


def text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.findall(".//a:t", NS))


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def run_props(run: ET.Element) -> ET.Element | None:
    return run.find("a:rPr", NS)


def color(rpr: ET.Element | None) -> str | None:
    if rpr is None:
        return None
    solid = rpr.find("a:solidFill", NS)
    if solid is None or not list(solid):
        return None
    return list(solid)[0].get("val") or list(solid)[0].get("lastClr")


def has_link(run: ET.Element) -> bool:
    rpr = run_props(run)
    return rpr is not None and rpr.find("a:hlinkClick", NS) is not None


def paragraph_head(paragraph: ET.Element) -> str:
    """Normalized text of a paragraph up to its first soft break (English half)."""
    out: list[str] = []
    for child in paragraph:
        if child.tag == f"{{{A_NS}}}br":
            break
        if child.tag in {f"{{{A_NS}}}r", f"{{{A_NS}}}fld"}:
            out.append(text(child))
    return normalized("".join(out))


def slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    if match is None:
        raise ValueError(name)
    return int(match.group(1))


def audit(deck: Path, kind: str, inventory: Path | None, issue_path: Path | None) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    report = {"slides": 0, "hyperlinks": 0, "soft_breaks": 0, "special_slides": 0}
    issue_data = json.loads(issue_path.read_text(encoding="utf-8")) if issue_path else None
    body_plain_paragraphs: list[str] = []
    nature_article_titles: set[str] = set()
    if issue_data is not None and kind == "nature":
        nature_article_titles = {
            normalized(item["title"])
            for section in issue_data["nature"]["sections"]
            for group in section.get("groups", [])
            if NATURE_GROUP_ROLES.get(group.get("name", "")) == "articles"
            for item in group.get("items", [])
        }

    with zipfile.ZipFile(deck) as zf:
        slide_names = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=slide_number,
        )
        report["slides"] = len(slide_names)
        all_text: list[str] = []
        external_targets: set[str] = set()

        for name in slide_names:
            sn = slide_number(name)
            root = ET.fromstring(zf.read(name))
            rels_name = f"ppt/slides/_rels/slide{sn}.xml.rels"
            if rels_name in zf.namelist():
                rels_root = ET.fromstring(zf.read(rels_name))
                external_targets.update(
                    node.get("Target", "")
                    for node in rels_root
                    if node.get("TargetMode") == "External" and node.get("Target")
                )
            all_text.append(text(root))
            slide_text = text(root)
            report["soft_breaks"] += len(root.findall(".//a:br", NS))
            report["hyperlinks"] += len(root.findall(".//a:hlinkClick", NS))
            for rpr in root.findall(".//a:rPr", NS):
                parent_run = next((run for run in root.findall(".//a:r", NS) if run.find("a:rPr", NS) is rpr), None)
                if parent_run is None or not text(parent_run):
                    continue
                latin = rpr.find("a:latin", NS)
                east = rpr.find("a:ea", NS)
                if latin is not None and latin.get("typeface") != "Calibri":
                    errors.append(f"slide {sn}: direct Latin font is {latin.get('typeface')}, expected Calibri")
                if east is not None and east.get("typeface") != "宋体":
                    errors.append(f"slide {sn}: direct East Asian font is {east.get('typeface')}, expected 宋体")

            for paragraph in root.findall(".//a:p", NS):
                paragraph_text = normalized(text(paragraph))
                runs = paragraph.findall("a:r", NS)
                linked_runs = [run for run in runs if has_link(run)]
                related_label = next(
                    (label for label in ("RELATED PERSPECTIVE", "RELATED RESEARCH ARTICLE") if paragraph_text.upper().startswith(label)),
                    "",
                )
                if linked_runs and paragraph.findall("a:br", NS) and not related_label and not DOI_RE.search(paragraph_text):
                    unlinked_text_runs = [run for run in runs if text(run).strip() and not has_link(run)]
                    if unlinked_text_runs:
                        errors.append(f"slide {sn}: ordinary bilingual title is only partly hyperlinked")
                if len(paragraph.findall("a:br", NS)) == 1:
                    parts: list[str] = [""]
                    for child in paragraph:
                        if child.tag == f"{{{A_NS}}}br":
                            parts.append("")
                        elif child.tag in {f"{{{A_NS}}}r", f"{{{A_NS}}}fld"}:
                            parts[-1] += text(child)
                    if len(parts) == 2 and len(parts[0]) >= 250:
                        en_len = len(parts[0])
                        zh_len = len(parts[1])
                        if zh_len / en_len < 0.18:
                            errors.append(f"slide {sn}: Chinese text is too short for a complete translation ({zh_len}/{en_len})")
                if related_label:
                    ppr = paragraph.find("a:pPr", NS)
                    if ppr is None or ppr.get("lvl") != "1":
                        errors.append(f"slide {sn}: {related_label} is not level 1")
                    if len(linked_runs) < 2:
                        errors.append(f"slide {sn}: {related_label} bilingual title lacks links")
            # Capacity gate: digest detail slides (one item each) and the cover
            # are exempt. Nature Articles pages use the dedicated two-primary-item
            # rule; every other list slide uses the visual-line budget.
            is_digest = slide_text.startswith("In Science Journals") or slide_text.startswith("In Other Journals")
            if sn > 1 and not is_digest:
                body_w = ESTIMATE_BODY_WIDTH.get(kind.capitalize(), 828.0)
                est_lines = 0
                body_paragraphs: list[ET.Element] = []
                for shape in root.findall(".//p:sp", NS):
                    ph = shape.find("./p:nvSpPr/p:nvPr/p:ph", NS)
                    txbody = shape.find("./p:txBody", NS)
                    if ph is None or txbody is None:
                        continue
                    if ph.get("type") not in {"body", "obj"} and ph.get("idx") != "1":
                        continue
                    for paragraph in txbody.findall("a:p", NS):
                        body_paragraphs.append(paragraph)
                        # Per-part size tracking: English and Chinese runs of a
                        # bilingual paragraph may differ in size (e.g. articles
                        # body 20 pt vs Chinese 20.5 pt), so estimate each part
                        # with the size of the runs that actually compose it.
                        part_sizes: list[list[float]] = [[]]
                        parts: list[str] = [""]
                        for child in paragraph:
                            if child.tag == f"{{{A_NS}}}br":
                                parts.append("")
                                part_sizes.append([])
                            elif child.tag in {f"{{{A_NS}}}r", f"{{{A_NS}}}fld"}:
                                parts[-1] += text(child)
                                rpr = run_props(child)
                                if rpr is not None and rpr.get("sz"):
                                    part_sizes[-1].append(int(rpr.get("sz")) / 100)
                        for part, sizes in zip(parts, part_sizes):
                            if not part.strip():
                                continue
                            size = max(sizes) if sizes else 18.0
                            est_lines += estimated_wrap_lines(part, size, body_w)
                body_plain_paragraphs.extend(
                    normalized(text(paragraph))
                    for paragraph in body_paragraphs
                    if normalized(text(paragraph))
                    and not paragraph.findall("a:br", NS)
                    and not any(has_link(run) for run in paragraph.findall("a:r", NS))
                )
                # Match an Articles page by exact paragraph head (English half
                # before the soft break) rather than a substring scan, so a
                # page such as Amendments & Corrections whose "Author
                # Correction:" titles repeat an Article title is not mistaken
                # for an Articles page.
                is_nature_articles = kind.lower() == "nature" and any(
                    paragraph_head(paragraph) in nature_article_titles
                    for paragraph in body_paragraphs
                )
                if is_nature_articles:
                    primary_count = sum(
                        1
                        for paragraph in body_paragraphs
                        if len(paragraph.findall("a:br", NS)) == 1
                        and any(has_link(run) for run in paragraph.findall("a:r", NS))
                        and not normalized(text(paragraph)).upper().startswith(
                            ("RELATED PERSPECTIVE", "RELATED RESEARCH ARTICLE")
                        )
                        and DOI_RE.search(normalized(text(paragraph))) is None
                    )
                    if primary_count > NATURE_ARTICLES_PRIMARY_LIMIT:
                        errors.append(
                            f"slide {sn}: Nature Articles page has {primary_count} primary items; "
                            f"limit is {NATURE_ARTICLES_PRIMARY_LIMIT}"
                        )
                elif est_lines > MAX_PAGE_LINES[kind]:
                    errors.append(f"slide {sn}: estimated {est_lines} visual lines exceed the limit of {MAX_PAGE_LINES[kind]}")

            for shape in root.findall(".//p:sp", NS):
                paragraphs = shape.findall(".//a:p", NS)
                if len(paragraphs) < 2:
                    continue
                detail_text = text(paragraphs[0])
                slide_text = text(root)
                if not (
                    slide_text.startswith("In Science Journals")
                    or slide_text.startswith("In Other Journals")
                ):
                    continue
                if detail_text.startswith("In Science Journals") or detail_text.startswith("In Other Journals"):
                    continue

                report["special_slides"] += 1
                title_p, intro_p = paragraphs[0], paragraphs[1]
                title_runs = title_p.findall("a:r", NS)
                if any(has_link(run) for run in title_runs):
                    errors.append(f"slide {sn}: special detail title contains a hyperlink")
                for run in title_runs:
                    rpr = run_props(run)
                    if rpr is not None and rpr.get("u") not in (None, "none"):
                        errors.append(f"slide {sn}: special detail title is underlined")
                        break
                if len(intro_p.findall("a:br", NS)) != 1:
                    errors.append(f"slide {sn}: full English/Chinese intro must contain exactly one soft break")
                if len(paragraphs) < 3:
                    errors.append(f"slide {sn}: missing DOI citation paragraph")
                    continue

                cite_p = paragraphs[2]
                ppr = cite_p.find("a:pPr", NS)
                if ppr is None or ppr.get("lvl") != "1":
                    errors.append(f"slide {sn}: DOI citation paragraph is not level 1")
                cite_text = text(cite_p)
                dois = DOI_RE.findall(cite_text)
                if not dois:
                    errors.append(f"slide {sn}: DOI citation contains no DOI")
                linked_dois: list[str] = []
                for run in cite_p.findall("a:r", NS):
                    run_text = text(run)
                    linked = has_link(run)
                    is_doi = bool(DOI_RE.fullmatch(run_text.strip()))
                    rpr = run_props(run)
                    if linked and not is_doi:
                        errors.append(f"slide {sn}: non-DOI citation text is hyperlinked: {run_text!r}")
                    if is_doi:
                        if not linked:
                            errors.append(f"slide {sn}: DOI is not hyperlinked: {run_text}")
                        else:
                            linked_dois.append(run_text.strip())
                        if color(rpr) not in ("FFC000", None):
                            warnings.append(f"slide {sn}: DOI color is {color(rpr)}, expected template yellow")
                    elif run_text.strip(" \t\r\n.,;:!?()[]{}，。；：！？") and color(rpr) not in ("FFFFFF", None):
                        errors.append(f"slide {sn}: citation prefix is not white: {run_text!r}")
                if len(linked_dois) != len(dois):
                    errors.append(f"slide {sn}: linked DOI count {len(linked_dois)} != DOI count {len(dois)}")

        if inventory:
            haystack = "\n".join(all_text)
            expected = [line.strip() for line in inventory.read_text(encoding="utf-8-sig").splitlines()]
            expected = [line for line in expected if line and not line.startswith("#")]
            missing = [item for item in expected if item not in haystack]
            report["inventory_expected"] = len(expected)
            report["inventory_missing"] = len(missing)
            errors.extend(f"inventory item missing: {item}" for item in missing)

        if issue_data is not None:
            issue = issue_data
            expected: list[tuple[str, str]] = []
            if kind == "nature":
                expected = [(item["title"], item["url"]) for section in issue["nature"]["sections"] for item in section["items"]]
            else:
                expected = [
                    (item["title"], item["url"])
                    for section in issue["science"]["sections"]
                    for subsection in section["subsections"]
                    for item in subsection["items"]
                    if not item.get("is_digest_wrapper")
                ]
                related_expected = [
                    (related["title"], related["url"])
                    for section in issue["science"]["sections"]
                    for subsection in section["subsections"]
                    for item in subsection["items"]
                    for related in item.get("related", [])
                    if related.get("kind", "").lower() in {"perspective", "research article"}
                ]
                known_urls = {url for _, url in expected}
                expected.extend((label, url) for label, url in related_expected if url not in known_urls)
                for digest in issue["science"]["digests"].values():
                    for item in digest["items"]:
                        expected.append((item["title"], item["url"]))
            def target_present(url: str) -> bool:
                doi = DOI_RE.search(url)
                if doi:
                    value = doi.group(0).rstrip(".)],;").lower()
                    return any(value in target.lower() for target in external_targets)
                return url in external_targets
            missing = [label for label, url in expected if not target_present(url)]
            report["source_expected"] = len(expected)
            report["source_missing"] = len(missing)
            errors.extend(f"source item missing: {value}" for value in missing)

            if kind == "nature":
                expected_labels = [
                    group["name"]
                    for section in issue["nature"]["sections"]
                    for group in (
                        section.get("groups")
                        or ([{"name": section["name"], "items": section.get("items", [])}] if section.get("items") else [])
                    )
                    if group.get("items")
                ]
            else:
                expected_labels = [
                    subsection.get("name") or section["name"]
                    for section in issue["science"]["sections"]
                    for subsection in section["subsections"]
                    if any(not item.get("is_digest_wrapper") for item in subsection["items"])
                ]
            expected_label_counts = Counter(expected_labels)
            rendered_label_counts = Counter(
                value for value in body_plain_paragraphs if value in expected_label_counts
            )
            report["group_labels_expected"] = sum(expected_label_counts.values())
            report["group_labels_rendered"] = sum(rendered_label_counts.values())
            for label, expected_count in expected_label_counts.items():
                rendered_count = rendered_label_counts[label]
                if rendered_count != expected_count:
                    errors.append(
                        f"group/subsection label {label!r} appears {rendered_count} times; "
                        f"expected {expected_count} (once per column occurrence)"
                    )

        if kind == "science" and report["special_slides"] == 0:
            warnings.append("no In Science Journals/In Other Journals detail slides detected")
        if report["hyperlinks"] == 0:
            errors.append("deck contains no hyperlinks")
        if report["soft_breaks"] == 0:
            errors.append("deck contains no soft line breaks")

    return errors, warnings, report


def audit_report(errors: list[str], warnings: list[str], report: dict[str, int]) -> int:
    print("REPORT", " ".join(f"{key}={value}" for key, value in report.items()))
    for item in warnings:
        print("WARNING", item)
    for item in errors:
        print("ERROR", item)
    print(f"RESULT errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Environment gate, configuration, and optional officecli checks
# ---------------------------------------------------------------------------

REQUIRED_IMPORTS = {"beautifulsoup4": "bs4", "lxml": "lxml", "Pillow": "PIL"}


def _utc_now() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _package_state() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for distribution, module in REQUIRED_IMPORTS.items():
        available = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if available else None
        except importlib.metadata.PackageNotFoundError:
            version = None
        result[distribution] = {"module": module, "available": available, "version": version}
    return result


def _interpreter_fingerprint(executable: str, version: str) -> str:
    value = f"{platform.node()}\0{executable}\0{version}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def configure_environment(config: Path, ppt_skill_name: str, ppt_skill_evidence: str, ppt_skill_path: Path | None = None) -> bool:
    """Write config/runtime.json for the current interpreter.

    Called only after the user has explicitly confirmed the Python interpreter
    and a PPTX-capable Skill has been verified in the current session.
    """
    executable = str(Path(sys.executable).resolve())
    version = platform.python_version()
    packages = _package_state()
    missing = [name for name, state in packages.items() if not state["available"]]
    if missing:
        print(f"ENV ERROR: required Python packages unavailable: {', '.join(missing)}")
        return False
    payload: dict[str, object] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "host": {"name": platform.node(), "system": platform.system(), "release": platform.release()},
        "python": {
            "executable": executable,
            "version": version,
            "implementation": platform.python_implementation(),
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "is_virtual_environment": sys.prefix != sys.base_prefix,
            "packages": packages,
            "fingerprint": _interpreter_fingerprint(executable, version),
            "user_confirmed": True,
        },
        "ppt_skill": {
            "name": ppt_skill_name,
            "available": True,
            "evidence": ppt_skill_evidence,
            "skill_md_path": str(ppt_skill_path.resolve()) if ppt_skill_path else None,
            "verified_at": _utc_now(),
            "requires_current_session_recheck": True,
        },
    }
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"configured": True, "config": str(config)}, ensure_ascii=False))
    return True


def check_environment(config: Path) -> bool:
    """Validate config/runtime.json against the current interpreter and packages."""
    if not config.is_file():
        print(f"ENV ERROR: configuration does not exist: {config}")
        return False
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ENV ERROR: configuration cannot be read: {exc}")
        return False
    python = data.get("python", {})
    executable = python.get("executable")
    ok = True
    if not executable or not Path(executable).is_file():
        print("ENV ERROR: recorded Python executable is missing")
        ok = False
    if executable and Path(sys.executable).resolve() != Path(executable).resolve():
        print(f"ENV ERROR: running with {sys.executable}, recorded {executable}")
        ok = False
    for name, state in (python.get("packages") or {}).items():
        if not state.get("available"):
            print(f"ENV ERROR: required package unavailable: {name}")
            ok = False
    ppt = data.get("ppt_skill", {})
    if not ppt.get("available") or not ppt.get("name"):
        print("ENV ERROR: PPT Skill availability was not recorded")
        ok = False
    if ok:
        print(f"ENV OK: {Path(executable).name} {python.get('version', '')} | ppt_skill={ppt.get('name')}")
    return ok


def officecli_validate(deck: Path) -> None:
    """Run advisory officecli checks when the command is available."""
    exe = shutil.which("officecli")
    if exe is None:
        print("OFFICECLI SKIPPED: officecli not found on PATH")
        return
    for args in (["validate", str(deck), "--json"], ["view", str(deck), "issues", "--json"]):
        result = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = result.stdout.strip() or result.stderr.strip()
        print(output)
        try:
            payload = json.loads(output)
            if not payload.get("success") and payload.get("warnings"):
                for warning in payload["warnings"]:
                    print("OFFICECLI ISSUE:", warning.get("message"))
        except json.JSONDecodeError:
            pass

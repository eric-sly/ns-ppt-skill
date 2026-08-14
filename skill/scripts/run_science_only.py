#!/usr/bin/env python3
"""Science-only pipeline: scrape -> translations worksheet -> build, from THREE user-saved MHTMLs.

This is the Science entry point of the per-journal pipeline. It reuses the parsing and
PPTX-building logic from the standard scripts but never touches Nature. Run it in stages:

    python run_science_only.py --stage scrape --science-toc <toc.mhtml> --in-science <isj.mhtml> --in-other <ioj.mhtml> --output <issue.json> --media-dir <dir> --translations <translations.json>
    python run_science_only.py --stage prepare --output <issue.json> --translations <translations.json>
    # fill every chinese_* field in translations.json yourself, then:
    python run_science_only.py --stage build --output <issue.json> --translations <translations.json> --output-dir <dir> [--science-template <pptx>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from journal_core import (
    audit,
    audit_report,
    build,
    check_environment,
    configure_environment,
    date_slug,
    load_source,
    officecli_validate,
    parse_science_digest,
    parse_science_toc,
    plan_science,
    write_issue,
    _write_cover,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config" / "runtime.json"


def prepare_worksheet(issue: dict[str, Any]) -> dict[str, Any]:
    science = issue["science"]
    # TOC cards carry an abstract excerpt that renders as a bilingual summary
    # under the title; cards without an excerpt stay title-only. Research
    # Article cards are excluded: their excerpt is a truncated teaser and the
    # complete introduction lives in the In Science Journals digest.
    exclude_subsections = {"Research Articles"}
    items: dict[str, dict[str, str]] = {
        item["id"]: {
            "english_title": item.get("title", ""),
            "chinese_title": "",
            "english_summary": "" if subsection["name"] in exclude_subsections else item.get("abstract", ""),
            "chinese_summary": "",
        }
        for section in science["sections"]
        for subsection in section["subsections"]
        for item in subsection["items"]
        if not item.get("is_digest_wrapper")
    }
    for section in science["sections"]:
        for subsection in section["subsections"]:
            for item in subsection["items"]:
                for related in item.get("related", []):
                    if related.get("kind", "").lower() != "perspective":
                        continue
                    related_id = related.get("doi") or related.get("url")
                    if related_id and related_id not in items:
                        items[related_id] = {
                            "english_title": related.get("title", ""),
                            "chinese_title": "",
                            "english_summary": "",
                            "chinese_summary": "",
                        }
    return {
        "schema_version": "1.0",
        "instructions": "Fill every chinese_* field with a complete translation. Do not edit IDs or English source fields.",
        "science": {
            "cover": {
                "english_title": science["cover"]["title"],
                "chinese_title": "封面",
                "english_summary": science["cover"].get("display_description", science["cover"]["description"]),
                "chinese_summary": "",
            },
            "items": items,
            "digests": {
                key: {
                    item["id"]: {
                        "english_topic": item["topic"],
                        "chinese_topic": "",
                        "english_headline": item["headline"],
                        "chinese_headline": "",
                        "english_intro": item["intro"],
                        "chinese_intro": "",
                    }
                    for item in digest["items"]
                }
                for key, digest in science["digests"].items()
            },
        },
    }


def load_or_prepare_worksheet(issue: dict[str, Any], path: Path) -> dict[str, Any]:
    """Load an existing filled worksheet, or create a fresh one and backfill missing IDs."""
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = prepare_worksheet(issue)
    fresh = prepare_worksheet(issue)
    science = data.setdefault("science", {})
    cover = science.setdefault("cover", {})
    fresh_cover = fresh["science"]["cover"]
    for field in ("chinese_title", "chinese_summary"):
        if not cover.get(field) and fresh_cover.get(field):
            cover[field] = fresh_cover[field]
    items = science.setdefault("items", {})
    for item_id, fresh_entry in fresh["science"]["items"].items():
        if item_id not in items:
            items[item_id] = fresh_entry
    digests = science.setdefault("digests", {})
    for key, fresh_digest in fresh["science"]["digests"].items():
        current = digests.setdefault(key, {})
        for item_id, fresh_entry in fresh_digest.items():
            if item_id not in current:
                current[item_id] = fresh_entry
    return data


def validate_translations(issue: dict[str, Any], translations: dict[str, Any]) -> None:
    missing: list[str] = []
    cover = translations["science"]["cover"]
    if cover.get("english_summary") and not cover.get("chinese_summary"):
        missing.append("science.cover.chinese_summary")
    for item_id, item in translations["science"]["items"].items():
        if not item.get("chinese_title"):
            missing.append(f"science.items.{item_id}.chinese_title")
    for digest_name, digest in translations["science"]["digests"].items():
        for item_id, item in digest.items():
            for field in ("chinese_topic", "chinese_headline", "chinese_intro"):
                if not item.get(field):
                    missing.append(f"science.digests.{digest_name}.{item_id}.{field}")
    if missing:
        preview = "\n".join(missing[:30])
        raise ValueError(f"translation worksheet incomplete ({len(missing)} fields):\n{preview}")


def stage_scrape(args: argparse.Namespace) -> int:
    sources = []
    for label, value in (("--science-toc", args.science_toc), ("--in-science", args.in_science), ("--in-other", args.in_other)):
        path = Path(value)
        if urllib.parse.urlparse(value).scheme in {"http", "https"} or path.suffix.lower() not in {".mhtml", ".mht"}:
            raise SystemExit(f"{label} must be a local user-saved .mhtml/.mht file")
        if not path.is_file():
            raise SystemExit(f"{label} file not found: {path}")
        sources.append((label, path))

    if args.archive_dir:
        probe = parse_science_toc(load_source(args.science_toc))
        slug = f"science-{date_slug(probe['date'])}"
        archive = Path(args.archive_dir) / slug
        archive.mkdir(parents=True, exist_ok=True)
        for attr, dest_name in (("science_toc", "science-toc.mhtml"), ("in_science", "in-science-journals.mhtml"), ("in_other", "in-other-journals.mhtml")):
            src = Path(getattr(args, attr))
            dest = archive / dest_name
            if not dest.is_file() or dest.read_bytes() != src.read_bytes():
                shutil.copy2(src, dest)
            print(f"ARCHIVED: {dest}")
            setattr(args, attr, str(dest))

    science_source = load_source(args.science_toc)
    science = parse_science_toc(science_source)
    science["digests"] = {
        "in_science_journals": parse_science_digest(load_source(args.in_science), "In Science Journals"),
        "in_other_journals": parse_science_digest(load_source(args.in_other), "In Other Journals"),
    }
    science["related_perspectives"] = [
        {
            "research_title": item["title"],
            "research_doi": item["doi"],
            "perspective_title": related["title"],
            "perspective_doi": related["doi"],
            "perspective_url": related["url"],
        }
        for section in science["sections"]
        for subsection in section["subsections"]
        for item in subsection["items"]
        for related in item["related"]
        if related["kind"].lower() == "perspective"
    ]
    issue: dict[str, Any] = {"schema_version": "1.0", "science": science}
    if args.media_dir:
        _write_cover(science, science_source, Path(args.media_dir), "science")
    file_labels = ("--science-toc", "--in-science", "--in-other")
    file_attrs = ("science_toc", "in_science", "in_other")
    final_sources = (science_source, load_source(args.in_science), load_source(args.in_other))
    issue["acquisition"] = {
        "mode": "user-manual-save",
        "agent_browser_automation": False,
        "files": {
            label: {
                "path": str(Path(getattr(args, attr)).resolve()),
                "sha256": hashlib.sha256(Path(getattr(args, attr)).read_bytes()).hexdigest(),
                "embedded_url": source.url,
            }
            for label, attr, source in zip(file_labels, file_attrs, final_sources)
        },
    }
    write_issue(issue, args.output)
    if args.translations:
        data = load_or_prepare_worksheet(issue, args.translations)
        args.translations.parent.mkdir(parents=True, exist_ok=True)
        args.translations.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    digest_counts = {key: digest["count"] for key, digest in science["digests"].items()}
    print(json.dumps({
        "output": str(args.output.resolve()),
        "date": science["date"],
        "volume": science["volume"],
        "issue": science["issue"],
        "sections": science["counts"]["sections"],
        "items": science["counts"]["items"],
        "digests": digest_counts,
        "related_perspectives": len(science["related_perspectives"]),
        "cover_image": science["cover"].get("image_file"),
        "translations": str(args.translations.resolve()) if args.translations else None,
    }, ensure_ascii=False))
    return 0


def stage_prepare(args: argparse.Namespace) -> int:
    issue = json.loads(args.output.read_text(encoding="utf-8"))
    if not args.translations:
        raise SystemExit("--translations is required for stage prepare")
    data = load_or_prepare_worksheet(issue, args.translations)
    args.translations.parent.mkdir(parents=True, exist_ok=True)
    args.translations.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    empty = sum(
        1 for item in data["science"]["items"].values()
        if not item.get("chinese_title")
    ) + sum(
        1 for digest in data["science"]["digests"].values()
        for item in digest.values()
        if not item.get("chinese_topic") or not item.get("chinese_headline") or not item.get("chinese_intro")
    )
    print(json.dumps({"output": str(args.translations.resolve()), "empty_item_fields": empty}, ensure_ascii=False))
    return 0


def stage_build(args: argparse.Namespace) -> int:
    issue = json.loads(args.output.read_text(encoding="utf-8"))
    if not args.translations:
        raise SystemExit("--translations is required for stage build")
    translations = json.loads(args.translations.read_text(encoding="utf-8"))
    validate_translations(issue, translations)
    science = issue["science"]
    cover = Path(science["cover"]["image_file"])
    if not cover.is_file():
        raise SystemExit(f"cover image missing: {cover}")
    pages = plan_science(science, translations["science"])
    output = Path(args.output_dir) / f"Science {date_slug(science['date'])}.pptx"
    build(args.science_template, output, "Science", science, pages, cover)
    errors, warnings, report = audit(output, "science", None, args.output)
    code = audit_report(errors, warnings, report)
    officecli_validate(output)
    print(json.dumps({"output": str(output.resolve()), "slides": len(pages), "audit_ok": not errors}, ensure_ascii=False))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("scrape", "prepare", "build"), help="pipeline stage (not needed with --configure)")
    parser.add_argument("--configure", nargs="+", metavar="ARG", help="configure environment: <ppt-skill-name> <evidence> [skill-md-path]")
    parser.add_argument("--science-toc", help="user-saved Science issue TOC MHTML path (stage scrape)")
    parser.add_argument("--in-science", help="user-saved In Science Journals MHTML path (stage scrape)")
    parser.add_argument("--in-other", help="user-saved In Other Journals MHTML path (stage scrape)")
    parser.add_argument("--archive-dir", type=Path, help="copy the source MHTMLs into <archive-dir>/science-<date>/ before re-parsing (stage scrape)")
    parser.add_argument("--output", type=Path, help="normalized UTF-8 issue JSON path (not needed with --configure)")
    parser.add_argument("--media-dir", type=Path, help="directory for extracted cover image (stage scrape)")
    parser.add_argument("--translations", type=Path, help="translation worksheet path (scrape/prepare/build)")
    parser.add_argument("--output-dir", type=Path, help="directory for the built PPTX (stage build)")
    parser.add_argument("--science-template", type=Path, default=SKILL_ROOT / "assets" / "science-template.pptx")
    args = parser.parse_args()
    if args.configure:
        if len(args.configure) < 2:
            raise SystemExit("--configure requires: <ppt-skill-name> <evidence> [skill-md-path]")
        skill_path = Path(args.configure[2]).resolve() if len(args.configure) > 2 else None
        return 0 if configure_environment(CONFIG_PATH, args.configure[0], args.configure[1], skill_path) else 1
    if args.stage is None:
        parser.error("--stage is required unless --configure is used")
    if args.output is None:
        parser.error("--output is required")
    if not check_environment(CONFIG_PATH):
        raise SystemExit("Environment gate failed; run --configure after confirming the Python interpreter")
    if args.stage == "scrape":
        return stage_scrape(args)
    if args.stage == "prepare":
        return stage_prepare(args)
    return stage_build(args)


if __name__ == "__main__":
    sys.exit(main())

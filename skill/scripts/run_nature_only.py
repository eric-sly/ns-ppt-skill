#!/usr/bin/env python3
"""Nature-only pipeline: scrape -> translations worksheet -> build, from ONE user-saved MHTML.

This is the Nature entry point of the per-journal pipeline. It reuses the parsing,
planning, and PPTX-building logic from the standard scripts but never touches
Science. Run it in stages:

    python run_nature_only.py --stage scrape  --nature <mhtml> --output <issue.json> --media-dir <dir> --translations <translations.json>
    python run_nature_only.py --stage prepare --output <issue.json> --translations <translations.json>
    # fill every chinese_* field in translations.json yourself, then:
    python run_nature_only.py --stage build   --output <issue.json> --translations <translations.json> --output-dir <dir> [--nature-template <pptx>]
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
    parse_nature,
    plan_nature,
    write_issue,
    _write_cover,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config" / "runtime.json"


def _entry(item: dict[str, Any]) -> dict[str, str]:
    summary_key = "summary" if item.get("display_summary") else "unused_summary"
    return {
        "english_title": item.get("title", ""),
        "chinese_title": "",
        "english_summary": item.get(summary_key, ""),
        "chinese_summary": "",
    }


def prepare_worksheet(issue: dict[str, Any]) -> dict[str, Any]:
    nature = issue["nature"]
    return {
        "schema_version": "1.0",
        "instructions": "Fill every chinese_* field with a complete translation. Do not edit IDs or English source fields.",
        "nature": {
            "cover": {
                "english_title": nature["cover"]["title"],
                "chinese_title": "",
                # 封面英文用完整 description（不经过 display_description 挑句），
                # 与完整中文翻译对等；封面渲染有内容感知缩放兜底。
                "english_summary": nature["cover"]["description"],
                "chinese_summary": "",
            },
            "items": {
                item["id"]: _entry(item)
                for section in nature["sections"]
                for item in section["items"]
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
    cover = data.setdefault("nature", {}).setdefault("cover", {})
    fresh_cover = fresh["nature"]["cover"]
    for field in ("chinese_title", "chinese_summary"):
        if not cover.get(field) and fresh_cover.get(field):
            cover[field] = fresh_cover[field]
    items = data["nature"].setdefault("items", {})
    for item_id, fresh_entry in fresh["nature"]["items"].items():
        if item_id not in items:
            items[item_id] = fresh_entry
    return data


def validate_translations(issue: dict[str, Any], translations: dict[str, Any]) -> None:
    missing: list[str] = []
    cover = translations["nature"]["cover"]
    for field in ("chinese_title", "chinese_summary"):
        english = cover.get("english_" + field.split("chinese_", 1)[1])
        if english and not cover.get(field):
            missing.append(f"nature.cover.{field}")
    for item_id, item in translations["nature"]["items"].items():
        if not item.get("chinese_title"):
            missing.append(f"nature.items.{item_id}.chinese_title")
        if item.get("english_summary") and not item.get("chinese_summary"):
            missing.append(f"nature.items.{item_id}.chinese_summary")
    if missing:
        preview = "\n".join(missing[:30])
        raise ValueError(f"translation worksheet incomplete ({len(missing)} fields):\n{preview}")


def stage_scrape(args: argparse.Namespace) -> int:
    path = Path(args.nature)
    if urllib.parse.urlparse(args.nature).scheme in {"http", "https"} or path.suffix.lower() not in {".mhtml", ".mht"}:
        raise SystemExit("--nature must be a local user-saved .mhtml/.mht file")
    if not path.is_file():
        raise SystemExit(f"--nature file not found: {path}")

    if args.archive_dir:
        probe = parse_nature(load_source(args.nature))
        slug = f"nature-{date_slug(probe['date'])}"
        archive = Path(args.archive_dir) / slug
        archive.mkdir(parents=True, exist_ok=True)
        dest = archive / "nature-issue.mhtml"
        if not dest.is_file() or dest.read_bytes() != path.read_bytes():
            shutil.copy2(path, dest)
        args.nature = str(dest)
        print(f"ARCHIVED: {dest}")

    source = load_source(args.nature)
    nature = parse_nature(source)
    issue: dict[str, Any] = {"schema_version": "1.0", "nature": nature}
    if args.media_dir:
        _write_cover(nature, source, Path(args.media_dir), "nature")
    final_path = Path(args.nature)
    issue["acquisition"] = {
        "mode": "user-manual-save",
        "agent_browser_automation": False,
        "files": {
            "nature": {
                "path": str(final_path.resolve()),
                "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
                "embedded_url": source.url,
            }
        },
    }
    write_issue(issue, args.output)
    if args.translations:
        data = load_or_prepare_worksheet(issue, args.translations)
        args.translations.parent.mkdir(parents=True, exist_ok=True)
        args.translations.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "date": nature["date"],
        "volume": nature["volume"],
        "issue": nature["issue"],
        "sections": nature["counts"]["sections"],
        "items": nature["counts"]["items"],
        "cover_image": nature["cover"].get("image_file"),
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
        1 for item in data["nature"]["items"].values()
        if not item.get("chinese_title") or (item.get("english_summary") and not item.get("chinese_summary"))
    )
    print(json.dumps({"output": str(args.translations.resolve()), "empty_item_fields": empty}, ensure_ascii=False))
    return 0


def stage_build(args: argparse.Namespace) -> int:
    issue = json.loads(args.output.read_text(encoding="utf-8"))
    if not args.translations:
        raise SystemExit("--translations is required for stage build")
    translations = json.loads(args.translations.read_text(encoding="utf-8"))
    validate_translations(issue, translations)
    nature = issue["nature"]
    cover = Path(nature["cover"]["image_file"])
    if not cover.is_file():
        raise SystemExit(f"cover image missing: {cover}")
    pages = plan_nature(nature, translations["nature"])
    output = Path(args.output_dir) / f"Nature {date_slug(nature['date'])}.pptx"
    build(args.nature_template, output, "Nature", nature, pages, cover)
    errors, warnings, report = audit(output, "nature", None, args.output)
    code = audit_report(errors, warnings, report)
    officecli_validate(output)
    print(json.dumps({"output": str(output.resolve()), "slides": len(pages), "audit_ok": not errors}, ensure_ascii=False))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("scrape", "prepare", "build"), help="pipeline stage (not needed with --configure)")
    parser.add_argument("--configure", nargs="+", metavar="ARG", help="configure environment: <ppt-skill-name> <evidence> [skill-md-path]")
    parser.add_argument("--nature", help="user-saved Nature issue MHTML path (stage scrape)")
    parser.add_argument("--archive-dir", type=Path, help="copy the source MHTML into <archive-dir>/nature-<date>/ before re-parsing (stage scrape)")
    parser.add_argument("--output", type=Path, help="normalized UTF-8 issue JSON path (not needed with --configure)")
    parser.add_argument("--media-dir", type=Path, help="directory for extracted cover image (stage scrape)")
    parser.add_argument("--translations", type=Path, help="translation worksheet path (scrape/prepare/build)")
    parser.add_argument("--output-dir", type=Path, help="directory for the built PPTX (stage build)")
    parser.add_argument("--nature-template", type=Path, default=SKILL_ROOT / "assets" / "nature-template.pptx")
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

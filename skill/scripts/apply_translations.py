#!/usr/bin/env python3
"""Merge agent-written Chinese translations into a translation worksheet.

The agent writes a JSON patch containing only the Chinese translations it
produced, then this script merges them into translations.json without touching
any English source fields or stable IDs.

Patch format (all keys optional; only provided chinese_* fields are updated):

    {
      "cover": {"chinese_title": "...", "chinese_summary": "..."},
      "items": {"<item-id>": {"chinese_title": "...", "chinese_summary": "..."}},
      "digests": {
        "in_science_journals": {
          "<item-id>": {"chinese_topic": "...", "chinese_headline": "...", "chinese_intro": "..."}
        }
      }
    }

Usage:

    python scripts/apply_translations.py work/translations.json --patch work/patch.json
    python scripts/apply_translations.py work/translations.json --check          # validate only
    python scripts/apply_translations.py work/translations.json --patch work/patch.json --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_ITEM_FIELDS = {
    "nature": ("chinese_title", "chinese_summary"),
    "science": ("chinese_title", "chinese_summary"),
}
DIGEST_FIELDS = ("chinese_topic", "chinese_headline", "chinese_intro")


def merge_patch(worksheet: dict[str, Any], patch: dict[str, Any]) -> int:
    updated = 0
    for journal in ("nature", "science"):
        section = worksheet.get(journal)
        if section is None:
            continue
        journal_patch = patch.get(journal) or patch
        if "cover" in journal_patch and "cover" in section:
            for field, value in journal_patch["cover"].items():
                if field.startswith("chinese_") and value and not section["cover"].get(field):
                    section["cover"][field] = value
                    updated += 1
        items_patch = journal_patch.get("items", {})
        for item_id, fields in items_patch.items():
            entry = section["items"].get(item_id)
            if entry is None:
                continue
            for field, value in fields.items():
                if field.startswith("chinese_") and value and not entry.get(field):
                    entry[field] = value
                    updated += 1
        if journal == "science":
            digests_patch = journal_patch.get("digests", {})
            for digest_key, items_patch in digests_patch.items():
                digest = section.get("digests", {}).get(digest_key)
                if digest is None:
                    continue
                for item_id, fields in items_patch.items():
                    entry = digest.get(item_id)
                    if entry is None:
                        continue
                    for field, value in fields.items():
                        if field.startswith("chinese_") and value and not entry.get(field):
                            entry[field] = value
                            updated += 1
    return updated


def missing_fields(worksheet: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for journal in ("nature", "science"):
        section = worksheet.get(journal)
        if section is None:
            continue
        cover = section["cover"]
        for field in ("chinese_title", "chinese_summary"):
            english = cover.get("english_" + field.split("chinese_", 1)[1])
            if english and not cover.get(field):
                missing.append(f"{journal}.cover.{field}")
        for item_id, item in section["items"].items():
            if not item.get("chinese_title"):
                missing.append(f"{journal}.items.{item_id}.chinese_title")
            if item.get("english_summary") and not item.get("chinese_summary"):
                missing.append(f"{journal}.items.{item_id}.chinese_summary")
        if journal == "science":
            for digest_key, digest in section.get("digests", {}).items():
                for item_id, item in digest.items():
                    for field in DIGEST_FIELDS:
                        if not item.get(field):
                            missing.append(f"science.digests.{digest_key}.{item_id}.{field}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worksheet", type=Path, help="translation worksheet JSON to update")
    parser.add_argument("--patch", type=Path, help="JSON patch with agent-written Chinese translations")
    parser.add_argument("--check", action="store_true", help="verify the worksheet is complete and exit")
    parser.add_argument("--output", type=Path, help="write the merged worksheet here instead of in place")
    args = parser.parse_args()

    if not args.worksheet.is_file():
        parser.error(f"worksheet not found: {args.worksheet}")
    worksheet = json.loads(args.worksheet.read_text(encoding="utf-8"))

    updated = 0
    if args.patch:
        if not args.patch.is_file():
            parser.error(f"patch not found: {args.patch}")
        patch = json.loads(args.patch.read_text(encoding="utf-8"))
        updated = merge_patch(worksheet, patch)
        output = args.output or args.worksheet
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(worksheet, ensure_ascii=False, indent=2), encoding="utf-8")

    missing = missing_fields(worksheet)
    if missing:
        preview = "\n".join(missing[:30])
        print(f"INCOMPLETE ({len(missing)} fields):\n{preview}")
        return 1
    print(json.dumps({"updated": updated, "worksheet": str(args.worksheet), "complete": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

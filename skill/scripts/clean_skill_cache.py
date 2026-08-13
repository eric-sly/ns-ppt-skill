#!/usr/bin/env python3
"""Clean runtime caches and test leftovers inside this Skill folder.

Every run of the entry scripts leaves `scripts/__pycache__/` (Python bytecode
cache). Test outputs written under the Skill's own `work/` folder (test-*.pptx
etc.) are leftovers from earlier iterations. This script removes those so the
Skill folder stays clean; production outputs never live here anyway because the
entry scripts take explicit absolute output paths.

Kept by default:
  - config/runtime.json  (written once by --configure)
  - backup/              (rollback history)

Usage:

    python scripts/clean_skill_cache.py            # remove caches and test leftovers
    python scripts/clean_skill_cache.py --check    # preview what would be removed
    python scripts/clean_skill_cache.py --backup   # also prune old backup/ dirs (keep newest)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def candidates(include_backup: bool) -> list[Path]:
    to_remove: list[Path] = []
    scripts_dir = SKILL_ROOT / "scripts"
    pycache = scripts_dir / "__pycache__"
    if pycache.is_dir():
        to_remove.append(pycache)
    if scripts_dir.is_dir():
        to_remove.extend(sorted(pyc for pyc in scripts_dir.rglob("*.pyc") if pyc.is_file()))
    work = SKILL_ROOT / "work"
    if work.is_dir():
        to_remove.extend(sorted(f for f in work.rglob("test-*") if f.is_file()))
    if include_backup:
        backup = SKILL_ROOT / "backup"
        if backup.is_dir():
            old_backups = sorted((d for d in backup.iterdir() if d.is_dir()), key=lambda d: d.name)
            to_remove.extend(old_backups[:-1])  # keep the newest for rollback
    return to_remove


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="list what would be removed without deleting")
    parser.add_argument("--backup", action="store_true", help="also prune old backup/ dirs (keep the newest)")
    args = parser.parse_args()

    items = candidates(args.backup)
    if not items:
        print("CLEAN: nothing to remove")
        return 0
    for path in items:
        print(("WOULD REMOVE" if args.check else "REMOVED") + f": {path}")
    if args.check:
        print(f"CLEAN: {len(items)} item(s) would be removed")
        return 0
    for path in items:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    print(f"CLEAN: removed {len(items)} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

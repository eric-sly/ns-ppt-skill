# Deterministic Operation Guide

## Contents

1. Environment gate and configuration
2. Dependencies
3. User-saved MHTML intake
4. MHTML authority and replay
5. Translation
6. Build, audit, and export
7. Stop conditions

## 1. Environment gate and configuration

Two entry scripts, one per journal, share `scripts/journal_core.py`:

```text
python scripts/run_nature_only.py   --help
python scripts/run_science_only.py  --help
```

Both scripts require `config/runtime.json` and print `ENV OK` before proceeding. The gate checks that the recorded Python executable matches the running interpreter and that the configuration records the required packages (`bs4`, `lxml`, `PIL`) plus PPT-capability metadata. The imports must also succeed when the scripts start. The configuration is provenance metadata; it does not prove that `officecli` is present on `PATH` in every later session.

On first use, or whenever the recorded interpreter, host, packages, or PPT capability changes, configure the environment explicitly. The user must confirm the intended Python interpreter first (never infer confirmation from silence). Prefer `officecli` as the recorded PPT capability when it is available. Then:

```text
"<confirmed-python>" scripts/run_nature_only.py --configure <ppt-skill-name> "<concise-evidence>" "<absolute-SKILL.md-path>"
```

The script writes `config/runtime.json` in the Skill folder. Never hand-edit that machine-local file. Reconfigure when the interpreter or recorded capability changes. A historical `available: true` value is metadata, not proof that an optional command is currently available.

## 2. Dependencies

The pipeline needs Python 3.10+ with `beautifulsoup4` (MHTML/HTML parsing), `lxml`, and `Pillow` (cover image conversion). Install them only after the user approves the confirmed Python environment:

```text
"<confirmed-python>" -m pip install beautifulsoup4 lxml Pillow
```

No browser-automation package is required or permitted for source acquisition.

## 3. User-saved MHTML intake

Proactively send the user the file request matching the journal being built and stop for their reply:

- **Nature only** (one file): open the latest Nature issue table of contents, expand the cover description until the control reads `show less`, scroll through the complete issue, and save as `网页，单个文件 (*.mhtml)` or the equivalent single-file option.
- **Science only** (three files): open the latest Science issue TOC (expand the description control until it reads `View Less`, scroll the full issue), the standalone `In Science Journals` detail page (scroll every item), and the standalone `In Other Journals` detail page; save each as single-file MHTML.

Ask the user to return the absolute file paths and map each path to its role. Do not accept a URL in place of a file. Do not use Playwright, Chrome control, computer-use, web download, `urllib`, `curl`, or another agent-controlled mechanism to open or save these pages. The user owns navigation, login/security checks, expansion, scrolling, and saving.

After the user replies, verify that the files exist, have `.mhtml` or `.mht` extensions, contain a multipart HTML part, correspond to the declared roles, and belong to the intended current issue. Expansion is primarily a user-operated acquisition step: the parser cannot reliably reconstruct the browser control state. If the parsed cover text is missing or clearly truncated, or a file is wrong, duplicated, or from a different issue, explain which file should be saved again.

## 4. MHTML authority and replay

### 4a. Archive the MHTML files

Run `scrape` with `--archive-dir` so the script copies the user-supplied MHTML files into a dedicated issue folder before re-parsing. `issue.json` then records stable, replayable paths inside the archive:

```text
work/sources/issues/nature-YYYY-MM-DD/nature-issue.mhtml
work/sources/issues/science-YYYY-MM-DD/science-toc.mhtml
work/sources/issues/science-YYYY-MM-DD/in-science-journals.mhtml
work/sources/issues/science-YYYY-MM-DD/in-other-journals.mhtml
```

The script derives the date slug from the parsed issue and prints an `ARCHIVED:` line per file. If `--archive-dir` is omitted, the source paths are recorded as-is; either way use the shell's copy command if archiving manually — never move, because the user may want to keep their original saves.

### 4b. Run the scraper against the archived paths

Nature:

```text
python scripts/run_nature_only.py --stage scrape \
  --nature "<user-saved nature-issue.mhtml>" \
  --archive-dir "work/sources/issues" \
  --output "work/issue.json" --media-dir "work/media" \
  --translations "work/translations.json"
```

Science:

```text
python scripts/run_science_only.py --stage scrape \
  --science-toc "<user-saved science-toc.mhtml>" \
  --in-science "<user-saved in-science-journals.mhtml>" \
  --in-other "<user-saved in-other-journals.mhtml>" \
  --archive-dir "work/sources/issues" \
  --output "work/issue.json" --media-dir "work/media" \
  --translations "work/translations.json"
```

`scrape` records each absolute path, SHA-256 hash, embedded page URL, and `user-manual-save` acquisition mode in `issue.json`, and writes the empty translation worksheet. Sections, in-section groups (Nature), and subsections (Science) are discovered from the actual TOC DOM; renamed or new columns are retained. Do not use ordinary HTML cache or live URLs for downstream work.

## 5. Translation

The `scrape` stage emits a worksheet with every required `chinese_*` field empty (`--translations`). Regenerate or backfill it at any time with `--stage prepare` (missing IDs are re-added; existing translations are preserved). The agent itself must then fill every `chinese_*` field; no script performs the translation step. `build` refuses to run while any required field is empty.

Write the translations as a JSON patch and merge it with `scripts/apply_translations.py` so English source fields and stable IDs stay untouched (the script only fills empty `chinese_*` fields, never overwrites, and validates completeness):

```text
python scripts/apply_translations.py work/translations.json --patch work/patch.json
python scripts/apply_translations.py work/translations.json --check   # validate only
```

Patch format — all keys optional, only provided `chinese_*` fields are updated:

```json
{
  "nature": {
    "cover": {"chinese_title": "...", "chinese_summary": "..."},
    "items": {"<item-id>": {"chinese_title": "...", "chinese_summary": "..."}}
  },
  "science": {
    "cover": {"chinese_summary": "..."},
    "items": {"<item-id>": {"chinese_title": "..."}},
    "digests": {
      "in_science_journals": {
        "<item-id>": {"chinese_topic": "...", "chinese_headline": "...", "chinese_intro": "..."}
      }
    }
  }
}
```

Translation rules:

- Preserve all selected sentences, claims, numbers, names, qualifiers, uncertainty, causal relations, and examples. Translate fully; do not summarize or condense.
- For Nature, fill `chinese_title` for every item and `chinese_summary` for every item whose `display_summary` is true; translate the cover title and summary.
- For Science, fill `chinese_title` for every TOC item (title-only), the cover `chinese_summary` (`chinese_title` stays `封面`), and `chinese_topic`, `chinese_headline`, `chinese_intro` for every digest item in both `in_science_journals` and `in_other_journals`.

## 6. Build, audit, and export

### 6a. Build (with automatic audit and optional officecli check)

```text
python scripts/run_nature_only.py --stage build \
  --output "work/issue.json" --translations "work/translations.json" --output-dir "work/output"

python scripts/run_science_only.py --stage build \
  --output "work/issue.json" --translations "work/translations.json" --output-dir "work/output"
```

The builder clones template role slides, paginates ordinary columns/groups/subsections by the per-journal estimated visual-line budget (`MAX_PAGE_LINES` = 20 for Nature, 12 for Science) plus the Nature density caps (at most two primary items on `Articles` pages, three on summarised pages, four on title-only pages, no mixing of summarised with title-only items), creates one slide per Science digest item, writes exact fonts/bullets/soft breaks, and applies fixed hyperlinks. A group/subsection label renders only before its first item in that column; continuation pages omit it. After writing the PPTX it automatically:

1. runs the structural audit (`REPORT ... RESULT errors=N warnings=M`), including source URL/DOI completeness, font/color/hyperlink checks, exactly one rendered label per group/subsection occurrence, the two-primary-item cap on Nature Articles pages, and the same per-journal line-capacity estimate used by the planner for other ordinary pages; audit errors make the command return a nonzero status, but the generated PPTX remains available; and
2. if `officecli` is on `PATH`, runs `officecli validate` and `officecli view ... issues`; otherwise it prints `OFFICECLI SKIPPED`. These external results are advisory and do not change the builder's exit status.

Review both outputs, fix clear content or structural problems when practical, and report any unresolved findings. Automated checks assist the agent and user; they do not replace final human review.

### 6b. PDF export

After reviewing the build and audit output, export the same-base-name PDF. Try the providers below in order; use the first one available in the current session.

1. **officecli PDF export** — `officecli view "<deck>" pdf -o "<out>.pdf"` (requires the exporter plugin; if it errors "No exporter plugin found", fall through).
2. **LibreOffice headless** — if `soffice` is on PATH:
   ```text
   soffice --headless --convert-to pdf --outdir "work/output" "work/output/Nature <date>.pptx"
   ```
3. **PowerPoint COM (Windows)** — if PowerPoint is installed:
   ```powershell
   $ppt = New-Object -ComObject PowerPoint.Application
   $ppt.Visible = $true
   $pres = $ppt.Presentations.Open("F:\...\Nature <date>.pptx", $true, $false, $false)
   $pres.SaveAs("F:\...\Nature <date>.pdf", 32)  # 32 = ppSaveAsPDF
   $pres.Close()
   $ppt.Quit()
   ```
4. If none of the above is available, stop and tell the user which capability is missing. Do not invent a fallback.

## Housekeeping

Every run leaves `scripts/__pycache__/` (bytecode cache) inside the Skill folder. Test leftovers from earlier iterations may also sit under the Skill's own `work/` folder. Clean them after a run (or before the next one):

```text
python scripts/clean_skill_cache.py            # remove caches and test leftovers
python scripts/clean_skill_cache.py --check    # preview what would be removed
python scripts/clean_skill_cache.py --backup   # also prune old backup/ dirs (keep newest)
```

`config/runtime.json` (written once by `--configure`) and `backup/` rollback history are kept by default. Production outputs (`issue.json`, translations, PPTX/PDF) never land in the Skill folder because the entry scripts take explicit absolute output paths.

## 7. Stop and review conditions

Hard-stop when the environment gate is invalid, the required MHTML file(s) are absent or the wrong role, MHTML parsing fails, a required title/URL/introduction/citation/DOI is missing, a required translation is blank, PPTX construction fails, or no export provider exists when a PDF is required.

Treat suspected truncation, structural-audit errors/warnings, and `officecli` findings as review signals. Fix clear problems when practical. If a usable deck remains and the user prefers a permissive workflow, report unresolved findings and let the agent/user review determine final acceptance.

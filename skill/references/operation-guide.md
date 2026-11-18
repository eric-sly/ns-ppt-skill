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

Agent capture (§3b) additionally needs the `playwright` package and an installed
Chrome or Edge. Playwright is deliberately **not** bundled with the skill and not
declared here: whether it is available depends on the host, so the agent checks
for it and, when it is missing, tells the user and lets them decide how to
provide it (or to save the pages manually). Without it only the manual-save path
is available.

## 3. Source intake

The MHTML set is the only downstream source in both modes. Decide the mode first
and say which one you used; never present a capture as a manual save.

### 3a. User-saved MHTML

Proactively send the user the file request matching the journal being built and stop for their reply:

- **Nature only** (one file): open the latest Nature issue table of contents, expand the cover description until the control reads `show less`, scroll through the complete issue, and save as `网页，单个文件 (*.mhtml)` or the equivalent single-file option.
- **Science only** (three files): open the latest Science issue TOC (expand the description control until it reads `View Less`, scroll the full issue), the standalone `In Science Journals` detail page (scroll every item), and the standalone `In Other Journals` detail page; save each as single-file MHTML.

Ask the user to return the absolute file paths and map each path to its role. Do
not accept a URL in place of a file.

After the user replies, verify that the files exist, have `.mhtml` or `.mht` extensions, contain a multipart HTML part, correspond to the declared roles, and belong to the intended current issue. If the parsed cover text is missing or clearly truncated, or a file is wrong, duplicated, or from a different issue, explain which file should be saved again.

### 3b. Agent capture

When the user asks the agent to fetch the issue itself (or when nobody is at the
browser), capture with the bundled script instead of improvising:

```text
"<python>" scripts/capture_issue.py --journal nature  --out-dir work/capture
"<python>" scripts/capture_issue.py --journal science --out-dir work/capture
```

Playwright is **not** bundled with this skill and is not one of its declared
dependencies. Before the first capture of a session, check that the interpreter
running the skill can import it (`python -c "import playwright"`). If it cannot,
say so plainly, tell the user that automated capture needs playwright, and let
them decide how to provide it (any method they prefer) or to save the pages
manually. Never install packages silently, and never record an unverified
success for a capture that did not run.

What it does per page: navigate → dismiss the cookie banner → click the
description control until it reads `show less` / `View Less` → scroll the whole
issue (three passes, so lazy images load) → `Page.captureSnapshot` (MHTML). For
Science it also follows the `In Science Journals` / `In Other Journals` links
from the TOC and captures each detail page.

It prints and writes `work/capture/capture-manifest.json` with, per role, the
requested URL, final URL, title, expansion state, card/section counts, byte size
and SHA-256. Check the manifest:

- `warnings` must be explained (a description that never expanded, or a card
  count far below a normal issue, means the capture is incomplete — re-capture
  with `--headed` and look at the page);
- `title` must be the intended issue;
- the roles must be the files you are about to scrape.

Then pass `--capture-manifest work/capture/capture-manifest.json` to `scrape`.
`issue.json` records `mode: agent-capture`, `agent_browser_automation: true`, the
tool/version/time and per-file capture evidence, and `scrape` fails if a supplied
file's hash differs from the manifest.

`--headed` shows the window; use it when a consent wall or a login must be
completed by hand, then re-run headless for the real capture.

### 3c. When capture fails

`capture_issue.py` treats these as failure, not as a usable snapshot:

- the page title or body is a bot-protection / consent screen (`Just a moment…`,
  `Checking your Browser…`, `Are you a robot`, …);
- the TOC has too few cards/sections (below `NATURE_MIN_CARDS` /
  `SCIENCE_MIN_SECTIONS`);
- a Science digest link is not on the TOC (use `--in-science-url` /
  `--in-other-url` if you know it), or the detail page has no entries.

On failure it:

- moves the suspect MHTML to `<out-dir>/rejected/` so nothing downstream can
  mistake it for a source;
- writes `capture-manifest.json` with `status: failed`, a `failures` list (role,
  reason, URL, save path, steps) and `message_for_user`;
- exits with status 3.

What the agent does then:

1. Do not scrape the rejected page and do not substitute another source.
2. Send `message_for_user` to the user, translated into their language; keep the
   URLs, save paths and steps verbatim.
3. One retry is allowed if you have a concrete new lever (`--headed`, an
   explicit digest URL). A second failure goes to the user.
4. When the user returns the paths, run `scrape` **without** `--capture-manifest`;
   the files are then recorded as `user-manual-save`.

Report the fallback in the completion summary: attempt, reason it failed, what
the user supplied.

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
  --nature "<nature-issue.mhtml>" \
  --capture-manifest "work/capture/capture-manifest.json" \
  --archive-dir "work/sources/issues" \
  --output "work/issue.json" --media-dir "work/media" \
  --translations "work/translations.json"
```

Science:

```text
python scripts/run_science_only.py --stage scrape \
  --science-toc "<science-toc.mhtml>" \
  --in-science "<in-science-journals.mhtml>" \
  --in-other "<in-other-journals.mhtml>" \
  --capture-manifest "work/capture/capture-manifest.json" \
  --archive-dir "work/sources/issues" \
  --output "work/issue.json" --media-dir "work/media" \
  --translations "work/translations.json"
```

Omit `--capture-manifest` for user-saved files; with it, `scrape` records the
agent-capture provenance and verifies hashes.

`scrape` records each absolute path, SHA-256 hash, embedded page URL, and the acquisition mode (`user-manual-save`, or `agent-capture` plus tool/time/capture evidence when `--capture-manifest` is passed) in `issue.json`, and writes the empty translation worksheet. Sections, in-section groups (Nature), and subsections (Science) are discovered from the actual TOC DOM; renamed or new columns are retained. Do not use ordinary HTML cache or live URLs for downstream work.

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
    "items": {"<item-id>": {"chinese_title": "...", "chinese_summary": "..."}},
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
- For Science, fill `chinese_title` for every TOC item and `chinese_summary` for every TOC item whose `abstract` excerpt is rendered as a bilingual summary line (all subsections except `Research Articles`, whose truncated teasers stay title-only), the cover `chinese_summary` (`chinese_title` stays `封面`), and `chinese_topic`, `chinese_headline`, `chinese_intro` for every digest item in both `in_science_journals` and `in_other_journals`.

## 6. Build, audit, and export

### 6a. Build (with automatic audit and optional officecli check)

```text
python scripts/run_nature_only.py --stage build \
  --output "work/issue.json" --translations "work/translations.json" --output-dir "work/output"

python scripts/run_science_only.py --stage build \
  --output "work/issue.json" --translations "work/translations.json" --output-dir "work/output"
```

The builder clones template role slides, paginates ordinary columns/groups/subsections by the per-journal estimated visual-line budget (`MAX_PAGE_LINES` = 20 for Nature, 12 for Science) plus the Nature density caps (at most two primary items on `Articles` pages, three on summarised pages, four on title-only pages, no mixing of summarised with title-only items), creates one slide per Science digest item, writes exact fonts/bullets/soft breaks, and applies fixed hyperlinks. A group/subsection label renders only before its first item in that column; continuation pages omit it. After writing the PPTX it automatically:

1. runs the structural audit (`REPORT ... RESULT errors=N warnings=M`), including source URL/DOI completeness, font/color/hyperlink checks, exactly one rendered label per group/subsection occurrence, the two-primary-item cap on Nature Articles pages, and the same per-journal line-capacity estimate used by the planner for other ordinary pages; audit errors make the command return a nonzero status, but the generated PPTX remains available;
2. if `officecli` is on `PATH`, runs `officecli validate` and `officecli view ... issues`; otherwise it prints `OFFICECLI SKIPPED`. These external results are advisory and do not change the builder's exit status; and
3. when a PowerPoint COM automation is available (Windows + PowerPoint), measures real text overflow with PowerPoint's layout engine and prints `OVERFLOW_SHAPES=n` (also `overflow_shapes` in the JSON summary); otherwise `OVERFLOW SKIPPED`. The line estimate cannot see glyph metrics, so this is the check that catches a page the planner thought fits. It is advisory, so fix the page (§6c) before exporting.

Review both outputs, fix clear content or structural problems when practical, and report any unresolved findings. Automated checks assist the agent and user; they do not replace final human review.

### 6c. Real-layout overflow

`OVERFLOW_SHAPES=0` means every text box fits. When it is not 0, list the paragraphs of the offending slide, then split it so the overflowing items move to their own page:

```text
pwsh -NoProfile -File scripts/measure_overflow.ps1 -Pptx "<deck>" -Slide <n>            # paragraph map with indices
pwsh -NoProfile -File scripts/split_overflow_page.ps1 -Pptx "<deck>" -Slide <n> -DryRun  # same map, no change
pwsh -NoProfile -File scripts/split_overflow_page.ps1 -Pptx "<deck>" -Slide <n> -SplitAfterParagraph <k>
```

Rules:

- split only at an item boundary: the paragraphs kept are complete groups of
  `label / title / summary`; the first paragraph moved to the new page must be the
  next group or subsection label, so the new page starts with its label;
- a continuation page keeps the same header; never duplicate a label that already
  appears on the previous page;
- re-run `measure_overflow.ps1` after the split and repeat until
  `OVERFLOW_SHAPES=0`, then export the PDF (§6b) — a PDF built before the fix has
  the wrong page count;
- if PowerPoint is unavailable, say so and leave the finding to the user instead
  of guessing.

### 6b. PDF export

After reviewing the build and audit output, export the same-base-name PDF. Try the providers below in order; use the first one available in the current session.

1. **officecli PDF export** — `officecli view "<deck>" pdf -o "<out>.pdf"` (requires the exporter plugin; if it errors "No exporter plugin found", fall through).
2. **LibreOffice headless** — if `soffice` is on PATH:
   ```text
   soffice --headless --convert-to pdf --outdir "work/output" "work/output/Nature <date>.pptx"
   ```
3. **PowerPoint COM (Windows)** — if PowerPoint is installed:
   ```text
   pwsh -NoProfile -File scripts/export_pdf.ps1 -Pptx "work/output/Nature <date>.pptx" -Pdf "work/output/Nature <date>.pdf"
   ```
   It prints `PDF:`, `PAGES:` and `BYTES:`; compare `PAGES` with the slide count.
   The equivalent inline snippet:
   ```powershell
   $ppt = New-Object -ComObject PowerPoint.Application
   $ppt.Visible = $true
   $ppt.DisplayAlerts = 1
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

Hard-stop when the environment gate is invalid, the required MHTML file(s) are absent or the wrong role, a capture manifest does not match the supplied bytes, a capture manifest warns that the page never expanded or is obviously incomplete, MHTML parsing fails, a required title/URL/introduction/citation/DOI is missing, a required translation is blank, PPTX construction fails, or no export provider exists when a PDF is required.

A failed agent capture (exit 3, `status: failed`) is not a hard stop for the task:
it is the signal to send the user `message_for_user` and wait for manual saves
(§3c). Pause there; resume with the user-supplied files.

Treat suspected truncation, structural-audit errors/warnings, and `officecli` findings as review signals. Fix clear problems when practical. If a usable deck remains and the user prefers a permissive workflow, report unresolved findings and let the agent/user review determine final acceptance.

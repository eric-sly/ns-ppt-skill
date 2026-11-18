---
name: ns-ppt-skill
description: >-
  Turn one journal's current issue into a complete bilingual (English/Chinese)
  weekly-guide PPTX/PDF by cloning the bundled templates: acquire the issue as
  MHTML (the user saves it, or the bundled capture script drives Chrome/Edge),
  translate every item fully, build, audit, and export. Nature needs one TOC
  file; Science needs its TOC plus the two digest detail pages. Use whenever
  the user asks to 做 Nature 周报 / Science 周报 / 期刊双语 PPT / 双语周报,
  "把 Nature/Science 做成 PPT", "生成这周的期刊 PPT", or mentions a weekly
  journal guide / bilingual deck with Nature or Science — even if they don't
  name the output format.
---

# NS-PPT Skill — Build Nature or Science Guide PPT

## Why this skill works this way (read this first)

- **The MHTML is the only ground truth, and it must cover the whole issue.**
  Either the user saves the pages, or `scripts/capture_issue.py` captures them
  with a real browser (expand the description control, scroll the entire issue,
  snapshot as MHTML). Both paths produce the same kind of file; downstream work
  reads that file, never a live URL, cached HTML, or a remembered column list.
  Never let a capture go out labelled as a manual save — `issue.json` records
  which mode produced each file.
- **Completeness outranks slide count.** Weekly columns, in-section groups, and
  subsections change constantly. The deck must show the week's full inventory,
  so a renamed column is never a reason to omit content; unknown names use a
  deterministic fallback template and stay included.
- **Construction is deterministic after translation.** The agent performs the
  translation directly; the builder then clones bundled template slides and
  applies fixed formatting. Automated checks flag likely structural problems,
  while the agent and user make the final quality decision.

## Three scripts, one shared core

- `scripts/run_nature_only.py` — Nature only: one MHTML (current-issue TOC).
- `scripts/run_science_only.py` — Science only: three MHTMLs (current-issue TOC,
  `In Science Journals` detail, `In Other Journals` detail).
- `scripts/capture_issue.py` — optional acquisition: drives Chrome/Edge,
  expands the page's controls, scrolls the whole issue and snapshots MHTML;
  writes `capture-manifest.json` for provenance.
- `scripts/journal_core.py` — shared library (parsing, PPTX construction,
  structural audit, environment gate, optional officecli checks). Never run it directly.

Both entry scripts expose three stages:

- `--stage scrape` — parse the MHTML, record paths/hashes/URLs plus the
  acquisition mode (`user-manual-save`, or `agent-capture` with tool, time and
  capture evidence when `--capture-manifest` is passed), and write the empty
  translation worksheet. Pass `--archive-dir` to have the script copy the source
  files into `work/sources/issues/<journal-date>/` automatically before re-parsing,
  so `issue.json` records stable replayable paths.
- `--stage prepare` — regenerate or backfill the translation worksheet
  (existing translations are preserved).
- `--stage build` — build the PPTX, run the structural audit, and use
  `officecli` as an additional check when it is available. Audit errors produce
  a nonzero exit status but leave the PPTX available for inspection;
  `officecli` findings are advisory.

Run `python scripts/run_<journal>_only.py --help` for the full command line.

## Environment gate

Every run checks `config/runtime.json` and prints `ENV OK` before proceeding.
The gate requires the recorded Python executable to match the running
interpreter and requires recorded package/PPT-capability metadata; the imports
themselves must also succeed when the scripts start. If the gate fails, confirm
the intended Python interpreter with the user, then run either entry script
with `--configure <ppt-skill-name> "<evidence>" "<SKILL.md path>"`. Prefer
`officecli` when available, but do not treat its absence during `build` as a
hard failure. Never hand-edit `config/runtime.json`.

## Source acquisition (two modes)

- **Agent capture** — `python scripts/capture_issue.py --journal nature --out-dir work/capture`
  (or `--journal science`). It opens the current-issue TOC, dismisses the cookie
  banner, clicks the description control until it reads `show less` / `View Less`,
  scrolls the whole issue, and snapshots MHTML with the DevTools protocol. For
  Science it also follows the `In Science Journals` and `In Other Journals` links.
  Read `capture-manifest.json`: `warnings` mean the capture is suspect (never
  expanded, too few cards), `title` must be the intended issue. Then pass
  `--capture-manifest` to `scrape`. Use `--headed` if a consent wall or login
  needs a human.
- **User manual save** — ask for the files only when the user prefers it or the
  site blocks automation:
  - **Nature** (one file): the current-issue TOC, description expanded to
    `show less`, whole issue scrolled, saved as `网页，单个文件 (*.mhtml)`.
  - **Science** (three files): the TOC (description expanded to `View Less`),
    the standalone `In Science Journals` page, and the standalone
    `In Other Journals` page.

Never accept a URL in place of a file, or a PDF, screenshot, copied text, or
browser cache folder. The bundled `assets/*.pptx` files are the binary visual
ground truth; never recreate their slides from blank layouts.

## Read before execution

1. [references/operation-guide.md](references/operation-guide.md) — exact
   commands and stop conditions.
2. [references/source-contract.md](references/source-contract.md) — dynamic
   section/group/subsection discovery, fields, and fallback rules.
3. [references/template-features.md](references/template-features.md) —
   geometry, typography, slide roles, page layout, and the exact text grammar
   (fonts, bullets, soft breaks, colors, hyperlinks).

Use `references/template-rules.json` as machine rules and
`references/template-spec.json` for the exhaustive OOXML inventory (query by
grep or targeted offset; never read the whole file).

## Mandatory pipeline

1. Environment gate passes (`ENV OK`).
2. Acquire the MHTML set for the journal: `capture_issue.py` (agent capture) or
   the user's own save. Check the manifest/title, then keep those files unchanged.
3. Run `scrape` with `--archive-dir` (and `--capture-manifest` for agent capture)
   so `issue.json` records stable replayable paths and honest provenance.
4. As the agent, fill every required `chinese_*` field — translate fully,
   never summarize and do not call an external translation service unless the
   user explicitly asks. Write a JSON patch and merge it with
   `scripts/apply_translations.py` so English source fields and IDs stay
   untouched. `build` refuses to run with blanks.
5. Run `build` — it clones template role slides and paginates mechanically.
   Ordinary list pages use fixed size tiers plus an estimated visual-line
   budget: `MAX_PAGE_LINES` is 20 for Nature and 12 for Science. Nature pages
   also follow hand-calibrated density caps: `Articles` groups hold at most
   two primary items per page (the final page may contain one) at the full
   24/20/20.5 pt tier; pages holding summarised items hold at most three;
   title-only pages hold at most four; summarised and title-only items never
   share a page. The builder then applies exact formatting and hyperlink
   scopes, audits structure, and runs an optional `officecli` check.
6. Review and report the audit output, fix clear problems when practical, then
   export the same-base-name PDF (officecli PDF plugin → LibreOffice headless →
   PowerPoint COM; see operation-guide §6b). Automated findings inform the
   final agent/user review; they are not an absolute acceptance gate.

## Non-negotiable rules

- Include every primary item discovered in the MHTML, every qualifying
  `RELATED PERSPECTIVE`, and every qualifying `RELATED RESEARCH ARTICLE`
  exactly once; completeness outranks slide count. The TOC shows each
  Perspective↔Research-Article pairing from both sides, and both lines render.
- A slide's title is the column (section) name read from the page; groups and
  subsection names render as bold white label lines inside the body placeholder
  in DOM order. Render each group/subsection label only once per column, before
  its first item; continuation pages omit the repeated label. Known names select
  their template roles; unknown ones fall back deterministically and stay included.
- Each Science digest item gets one slide with its full standalone-page
  introduction.
- English and Chinese paired text share one paragraph and exactly one soft
  break (`a:br`, Shift+Enter).
- Use Calibri for Latin and 宋体 for Chinese; follow the full text grammar in
  template-features.md (colors, underline, hyperlink scopes, bullets).
- Never bypass the environment gate; never scrape a live URL, a cached HTML
  folder, or anything other than the MHTML set for this issue. Capture only
  through `scripts/capture_issue.py` (or the user's own save), and record which
  of the two produced each file.

## Housekeeping

Every run leaves `scripts/__pycache__/` (Python bytecode cache). Clean it, and
any test leftovers under the Skill's own `work/` folder, after a run (or before
the next one):

    python scripts/clean_skill_cache.py            # remove caches and test leftovers
    python scripts/clean_skill_cache.py --check    # preview what would be removed
    python scripts/clean_skill_cache.py --backup   # also prune old backup/ dirs (keep newest)

`config/runtime.json` (written once by `--configure`) and `backup/` rollback
history are kept by default. Production outputs never land in the Skill folder
because the entry scripts take explicit absolute output paths.

## No-vision rule

A model without vision may execute downstream work because the builder clones
machine-specified templates and the checks are structural. It must not claim that
the model itself visually inspected the final slides, or that it verified an
agent capture by eye, unless that check actually occurred; report instead what
the manifest, the audit and officecli measured.

## Completion report

Report the embedded source URLs, MHTML paths and hashes, the acquisition mode
(`user-manual-save` or `agent-capture`, with tool/time and any capture warnings),
dynamic section/group/subsection counts, related-Perspective and
related-Research-Article counts, slide counts, structural-audit result,
`officecli` status (`passed`, `findings`, or `skipped`), unresolved findings,
and final PPTX/PDF paths or export status.

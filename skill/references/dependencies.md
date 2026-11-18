# External Dependencies Contract

This file is the single source of truth for what this Skill needs. A generic agent reads this first to decide whether it can run the pipeline in the current session.

## 1. Runtime hard dependencies (scripts will fail without these)

| Dependency | Version | Purpose | Where used |
|---|---|---|---|
| Python | 3.10+ | Scripts use PEP 604 `X \| Y` union syntax at runtime | `scripts/journal_core.py` |
| beautifulsoup4 | >=4.10 | MHTML / HTML parsing | `scripts/journal_core.py` |
| lxml | >=4.6 | HTML parsing support for BeautifulSoup | `scripts/journal_core.py` |
| Pillow | >=9.0 | Cover image conversion to PNG | `scripts/journal_core.py` |

Install only after the user confirms the Python interpreter:

```text
"<confirmed-python>" -m pip install beautifulsoup4 lxml Pillow
```

`playwright` is optional and deliberately not bundled or declared: agent capture
(§3b) needs it, the agent must detect whether the interpreter can import it, and
if it cannot, the agent tells the user and lets them decide how to provide it —
never installs it silently. Nothing else (no Selenium, no requests/urllib
downloader) is used for source acquisition.

## 2. Agent capability and optional validation tools

### 2a. LLM translation capability — REQUIRED, provided by the agent itself

The `scrape`/`prepare` stages only emit a worksheet with empty `chinese_*` fields. The agent itself translates every required English field into Chinese and writes the filled JSON back before running `build`. No separate translation service, translation API, or translation package is required; the translation work is part of the active agent run. If the agent cannot produce Chinese translations, the pipeline stops here.

### 2b. PPTX Office validation — OPTIONAL, advisory

After producing the deck, `build` tries an external PPTX validation pass when `officecli` is available on `PATH`. If it is unavailable, the script prints `OFFICECLI SKIPPED`. Findings are review signals and do not by themselves determine whether the deck may be delivered or exported.

**Clarification:** `officecli` does NOT participate in PPTX construction. `scripts/journal_core.py` builds the decks directly by cloning the bundled template files. `officecli` is only used for (i) structural validation and (ii) optional PDF export.

The agent should report whether the check passed, produced findings, or was skipped. Final acceptance belongs to the agent/user review.

### 2c. PDF export tool — REQUIRED only for PDF output

See `references/operation-guide.md` §6b for the ordered fallback chain (officecli PDF plugin → LibreOffice → PowerPoint COM). If no exporter exists, preserve and deliver the PPTX and report that PDF export was unavailable.

## 3. Source acquisition — two supported modes

The MHTML set for the journal being built is the only downstream source. Either
mode produces it; the mode and its evidence are recorded in `issue.json`.

### 3a. User manual save (default when the user offers files)

The user saves each page with the browser's single-file MHTML option and supplies
absolute local paths mapped to the roles below. Use this when the site asks for a
login, a consent step only a human can complete, or the user simply prefers it.

- **Nature only** (one file): Nature current issue TOC → `nature-issue.mhtml`.
- **Science only** (three files):
  1. Science current issue TOC → `science-toc.mhtml`
  2. Science `In Science Journals` standalone detail page → `in-science-journals.mhtml`
  3. Science `In Other Journals` standalone detail page → `in-other-journals.mhtml`

See `references/operation-guide.md` §3a for the save instructions given to the user.

### 3b. Agent capture (`scripts/capture_issue.py`)

The agent drives the installed Chrome/Edge through Playwright: it opens the TOC,
dismisses the cookie banner, expands the description control until it reads
`show less` (Nature) / `View Less` (Science), scrolls the whole issue so lazy
content loads, and snapshots the page with the DevTools protocol
(`Page.captureSnapshot`) into MHTML. Science capture follows the `In Science
Journals` / `In Other Journals` links from the TOC and captures each detail page
the same way.

Playwright is not bundled here and not declared as a dependency: check whether the
interpreter can import it, and if it cannot, ask the user how to proceed instead
of installing anything yourself.

```text
"<python>" scripts/capture_issue.py --journal nature  --out-dir work/capture
"<python>" scripts/capture_issue.py --journal science --out-dir work/capture
```

It writes the role files plus `capture-manifest.json` (URL, title, expansion
state, card/section counts, SHA-256). Pass that manifest to `scrape` with
`--capture-manifest`; `issue.json` then records `mode: agent-capture`,
`agent_browser_automation: true`, the tool/version/time, and rejects any file
whose hash does not match the manifest. Add `--headed` when a consent wall needs
a human, and check the manifest `warnings` (for example a description that never
reached `show less`, or a card count far below a normal issue).

### 3c. Rules that apply to both modes

- Downstream work reads the MHTML files, never a live URL, a cached HTML folder,
  a PDF, a screenshot, or copied text.
- The capture must cover the whole issue: expand the description control first,
  then scroll to the end before saving or snapshotting.
- Report which mode was used. Never describe an agent capture as a manual save.

## 4. Not required (do not install even if older artifacts mention them)

- **Selenium, requests, urllib3 downloaders, curl** — never used for source
  acquisition. Only `playwright` (§3b) may drive a browser, and only through
  `scripts/capture_issue.py`; the MHTML it produces is still the sole source.

## 5. Environment gate

Both entry scripts (`scripts/run_nature_only.py`, `scripts/run_science_only.py`) check `config/runtime.json` on every run and print `ENV OK` before proceeding. The gate verifies:

1. The running Python executable matches the executable recorded during configuration; required imports must also succeed when the scripts start.
2. The user has explicitly confirmed that interpreter (recorded as `user_confirmed: true`).
3. PPT-capability metadata was recorded with concrete evidence (Skill name and, when supplied, a resolved `SKILL.md` path).

`config/runtime.json` is written via the `--configure` flag of either entry script after the user confirms the interpreter. That file is machine-local, gitignored, and never distributable. It records provenance and does not guarantee that an optional validation command remains available in later sessions. Repeat configuration whenever the gate fails or the recorded environment changes.

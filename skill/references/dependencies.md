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

No browser-automation package is required or permitted. Playwright, Selenium, urllib downloaders, and curl are all forbidden for source acquisition.

## 2. Agent capability and optional validation tools

### 2a. LLM translation capability — REQUIRED, provided by the agent itself

The `scrape`/`prepare` stages only emit a worksheet with empty `chinese_*` fields. The agent itself translates every required English field into Chinese and writes the filled JSON back before running `build`. No separate translation service, translation API, or translation package is required; the translation work is part of the active agent run. If the agent cannot produce Chinese translations, the pipeline stops here.

### 2b. PPTX Office validation — OPTIONAL, advisory

After producing the deck, `build` tries an external PPTX validation pass when `officecli` is available on `PATH`. If it is unavailable, the script prints `OFFICECLI SKIPPED`. Findings are review signals and do not by themselves determine whether the deck may be delivered or exported.

**Clarification:** `officecli` does NOT participate in PPTX construction. `scripts/journal_core.py` builds the decks directly by cloning the bundled template files. `officecli` is only used for (i) structural validation and (ii) optional PDF export.

The agent should report whether the check passed, produced findings, or was skipped. Final acceptance belongs to the agent/user review.

### 2c. PDF export tool — REQUIRED only for PDF output

See `references/operation-guide.md` §6b for the ordered fallback chain (officecli PDF plugin → LibreOffice → PowerPoint COM). If no exporter exists, preserve and deliver the PPTX and report that PDF export was unavailable.

## 3. User input dependencies (required, only the user can supply)

The user must manually save the MHTML file(s) for the journal being built via their own browser and supply absolute local paths mapped to the roles below. The agent must never browse, automate, download, or substitute these files.

- **Nature only** (one file): Nature current issue TOC → `nature-issue.mhtml`.
- **Science only** (three files):
  1. Science current issue TOC → `science-toc.mhtml`
  2. Science `In Science Journals` standalone detail page → `in-science-journals.mhtml`
  3. Science `In Other Journals` standalone detail page → `in-other-journals.mhtml`

See `references/operation-guide.md` §3 for the exact save instructions given to the user.

## 4. Not required (do not install even if older artifacts mention them)

- **playwright** — not declared, never checked, and forbidden for source acquisition. Any `runtime.json` produced by an older version may list it; that record is stale and must be regenerated with `--configure`.
- **Selenium, requests, urllib3 downloaders** — same as above.

## 5. Environment gate

Both entry scripts (`scripts/run_nature_only.py`, `scripts/run_science_only.py`) check `config/runtime.json` on every run and print `ENV OK` before proceeding. The gate verifies:

1. The running Python executable matches the executable recorded during configuration; required imports must also succeed when the scripts start.
2. The user has explicitly confirmed that interpreter (recorded as `user_confirmed: true`).
3. PPT-capability metadata was recorded with concrete evidence (Skill name and, when supplied, a resolved `SKILL.md` path).

`config/runtime.json` is written via the `--configure` flag of either entry script after the user confirms the interpreter. That file is machine-local, gitignored, and never distributable. It records provenance and does not guarantee that an optional validation command remains available in later sessions. Repeat configuration whenever the gate fails or the recorded environment changes.

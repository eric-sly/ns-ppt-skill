# Acceptance and Review Policy

This file separates hard pipeline requirements from advisory quality signals. Executable layout rules live in `template-rules.json`; source semantics live in `source-contract.md`.

## Hard requirements

- The agent asks the user to manually save the MHTML file(s) for the journal being built (Nature: one TOC; Science: TOC plus the two digest detail pages); only those user-provided local files are parsed.
- Current Nature/Science sections are discovered dynamically; renamed or new columns are retained through deterministic fallback layouts.
- Full selected English content is translated without summarization.
- Each group/subsection label appears exactly once per column occurrence, before its first item; continuation pages do not repeat it.
- Ordinary slides stay within the per-journal estimated visual-line budget (`MAX_PAGE_LINES` = 20 for Nature, 12 for Science) and the Nature density caps: at most two primary items on `Articles` pages, at most three on pages holding summarised items, at most four on title-only pages, and summarised and title-only items never mix on one page.
- Digest slides have exactly one entry and use their standalone detail-page introduction and citation.
- Related Perspectives and Related Research Articles remain subordinate; each contributes two estimated visual lines.
- PPTX construction completes and the structural audit runs. Audit errors return a nonzero command status but do not delete the generated deck.

## Advisory review signals

- Review structural-audit findings about fonts, bullets, soft breaks, colors, hierarchy, geometry, hyperlink scopes, source coverage, and line capacity.
- Run `officecli` checks when available; treat `passed`, `findings`, and `skipped` as reportable states rather than an absolute delivery gate.
- Fix clear content or structural problems when practical. Report unresolved findings with the output.

## Final acceptance

The agent performs translation, construction, and automated checks. The user remains the final reviewer of translation quality, completeness, and visual fitness. A warning or optional-tool finding does not automatically invalidate an otherwise usable deck.

## Content ground truth

The user-saved MHTML page(s) for the journal being built are the only content ground truth. Any previously produced deck (including decks accepted in past runs) is not a content source and must not be copied from. If a past deck appears to violate a rule above, the rule wins — the past deck was an accepted anomaly, not a template to imitate.

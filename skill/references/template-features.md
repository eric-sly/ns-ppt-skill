# Template Feature Reference

## Contents

1. Authoritative assets
2. Shared design system
3. Nature slide roles
4. Science slide roles
5. Text and hyperlink grammar
6. Deterministic sizing
7. Features that must be inherited

## 1. Authoritative assets

Never redraw these templates. Always clone slides from `assets/nature-template.pptx` and `assets/science-template.pptx`. This preserves the black background, masters, layouts, placeholder geometry, theme, image position, z-order, package relationships, and all non-text properties without visual judgment.

Reference asset hashes are recorded in `references/template-rules.json` for diagnosing template drift; the builder does not reject a template automatically on hash mismatch. The exhaustive OOXML inventory is `references/template-spec.json`; it records theme fonts and colors, masters, layouts, every template slide, every shape and picture transform, paragraph properties, run properties, bullets, soft breaks, autofit, relationships, transitions, and timings.

## 2. Shared design system

- Canvas: 12,192,000 × 6,858,000 EMU, 16:9.
- Background: black.
- Direct generated fonts: Calibri for Latin; 宋体 for Chinese.
- Primary text: white `FFFFFF`.
- Emphasis, ordinary linked titles, section category, and DOI: yellow `FFC000`.
- Header occupies the full-width top band at `y=0`, height `614363` EMU. Its one
  paragraph is split by a right-aligned tab stop (`a:tabLst` with `algn="r"` at
  `title width − 2×91440 − 50000` EMU): journal + yellow column name (no
  underline) on the left, the volume/date metadata on the right hugging the
  placeholder's right edge regardless of column-name length. The tab is the
  `\t` character inside its own run, never a bare `<a:tab/>` element (not valid
  OOXML).
- Every content slide retains the small issue-cover thumbnail at bottom left.
- Main bullet: level 0, `•`, left margin `228600`, hanging indent `-228600`.
- Subordinate lines use master-inherited level 1; do not write `buNone`.
- Bilingual stacking uses exactly one `<a:br/>` Shift+Enter inside one paragraph.
- Ordinary pages are split by an estimated visual-line budget (see §6) with
  fixed size tiers per page style. Nature `Articles` pages are the explicit
  exception: they use at most two primary items per page. Each RELATED line
  contributes two estimated visual lines on line-budgeted pages.

## 3. Nature slide roles

Clone only the following source slides:

| Output role | Template slide | Notes |
|---|---:|---|
| Cover | 1 | Large issue cover left; copy right |
| This Week mixed sections | 2 | Full-width body; Editorial and World View groups |
| Research Highlight | 3 | Summary format |
| News list | 4 | Dense list format; News group, may mix e.g. a News Q&A card |
| News Feature | 5 | Longer summaries; also News Q&A |
| Books, Comment, Correspondence, Work | 6 | Title or commentary format; also Obituary |
| News & Views | 7 | Summarized entries |
| Articles, corrections, technology | 8 | Article format |

Pages follow the column-aggregated layout. The slide title is the column (section) name read from the page, yellow without underline; each in-column group renders one bold white label before its first item in DOM order, and continuation pages omit the label. Pages split when the estimated visual-line budget (see §6) is exceeded, except that the `Articles` group stays on dedicated pages with at most two primary items per page. Header syntax is `Nature`, the yellow (non-underlined) column name, a right-aligned tab, then `Volume {volume} Issue {issue}, {date}` (see §2). Use 42 pt for names up to 18 characters, 36 pt up to 28, and 32 pt beyond; keep 32 pt for `Amendments & Corrections`. Because the volume metadata shares the header line, the size also drops so the combined journal + column name + metadata text stays inside the tab position: at most ~54 combined characters at 42 pt, ~63 at 36 pt, ~71 at 32 pt, and ~81 at 28 pt (e.g. `News in Focus` totals 57 characters and therefore renders at 36 pt).

The accepted cover differs from the original template: place the English and Chinese cover title on one line, white, bold, 30 pt, without underline; place the English display description and complete Chinese translation at 24 pt with one soft break.

## 4. Science slide roles

| Output role | Template slide | Notes |
|---|---:|---|
| Cover | 1 | Large cover left; 36 pt copy right |
| Editorial | 2 | Editorial column page |
| Commentary, Essays, Working Life | 3 | Linked bilingual title list with abstract summaries |
| News, Research, Reviews | 4 | Linked bilingual title list with abstract summaries |
| In Science Journals detail | 9 | One item per page |
| In Other Journals detail | 13 | One item per page |

Do not clone Science template slide 7; it contains an unrelated dictionary text box. Slide 9 is the clean `In Science Journals` source.

Pages follow the column-driven layout. The slide title is the section (column) name read from the page; each subsection renders one bold white label before its first item in DOM order, and continuation pages omit the label. Pages split when the estimated visual-line budget (see §6) is exceeded. Template roles are selected per subsection first and per section as a fallback; a section and subsection that are both unknown use the generic slide 2 title list and remain included.

Ordinary list slides render each TOC item's abstract excerpt as a bilingual summary line under its title; `Research Articles` excerpts are truncated TOC teasers and are excluded, and cards without an excerpt render title-only.

Science header size is 34 pt for names up to 18 characters, 30 pt up to 28, and 26 pt beyond; the size also drops when the combined journal + subsection + metadata text exceeds the tab position (~66 combined characters at 34 pt, ~75 at 30 pt, ~87 at 26 pt). Ordinary headers begin with `Science`; the metadata follows a right-aligned tab (see §2) so it stays flush right. Digest headers omit the redundant journal word and begin directly with `In Science Journals` or `In Other Journals`. Metadata syntax is `Volume {volume} | Issue {issue} | {date}`.

Science cover body is one paragraph: bold `COVER ` plus English description, one soft break, Chinese translation; all at 36 pt.

## 5. Text and hyperlink grammar

Ordinary title paragraph:

1. English title: yellow, bold, underlined, linked to the article.
2. One soft break.
3. Chinese title: same formatting and same hyperlink target.

Ordinary summary paragraph:

1. Level-0 bullet.
2. Exact English TOC summary.
3. One soft break.
4. Complete Chinese translation.

Column group/subsection label line: a bold white paragraph with no bullet, rendered once before that group's first item in the column. Do not repeat it on continuation pages.

Digest detail title is a single unlinked, non-underlined paragraph: English topic, Chinese topic, English headline, Chinese headline. Use two non-breaking spaces between units. The topic uses the template's caps treatment.

Digest intro is a level-0 bullet with complete English, one soft break, and complete Chinese. Citation is a separate inherited level-1 bullet holding only the first citation entry: text up to and including the first DOI, which alone is yellow, underlined, and linked to its DOI URL; any trailing `see also` entry is dropped. Prefix, journal, and page are white.

`RELATED PERSPECTIVE` (under a Research Article) and `RELATED RESEARCH ARTICLE` (under a Perspective) are inherited level-1 lines. The label is uppercase white bold; the bilingual related title is yellow, underlined, and linked. Neither is a separate primary item; each contributes two estimated visual lines to page capacity.

All issue-cover pictures link to the issue table of contents.

## 6. Deterministic sizing

Ordinary list pages use fixed size tiers selected by page style and item count (`base_sizes`): research/title-list pages 25 pt for up to two items and 23 pt beyond, list 22 pt, articles 24/20/20.5 pt (title/English summary/Chinese summary) — the compact 21/18/18.5 pt tier only applies if an Articles page ever exceeds two items — and a related-line shrink rule (18.5 pt) when a page carries two or more `RELATED` lines. Most pages split by an estimated visual-line budget: every label line and every English/Chinese title line counts as one visual line, wrapping is estimated from CJK full-width vs Latin half-width glyph widths, each `RELATED` line counts as two, and `MAX_PAGE_LINES` is 20 for Nature and 12 for Science. Nature pages additionally follow hand-calibrated density caps calibrated against an edited reference deck: `Articles` groups hold at most two primary items on dedicated pages (the final page may contain one); pages holding summarised items hold at most three; title-only pages hold at most four; summarised and title-only items never share a page. The structural audit checks the two-item Articles cap and applies the per-journal line estimate to other list pages. Digest detail slides and the cover keep their own explicit sizing.

Digest pages choose a tier from the combined character count of the four title units, English intro, Chinese intro, and citation:

- Under 700: title 32, body 24/24, citation 20 pt.
- 700–1100: title 24, body 24/24, citation 20 pt, 92.5% autofit.
- 1101–1400: title 24, English 20, Chinese 24, citation 18 pt.
- Above 1400: title 20, English 18, Chinese 20, citation 16 pt.

Use 10% line-spacing reduction for digest pages. This reproduces the accepted deck's content-sensitive approach without a visual model.

## 7. Features that must be inherited

Keep the original theme, master, layout link, placeholder identity, black background, picture shape, crop, z-order, and package metadata by cloning the exact role slide. Preserve the body placeholder's list style. For level-1 citation and related lines, write only `lvl=1` and inherit the bullet from the master. Never rebuild a blank presentation from approximate coordinates.

Reset the body placeholder's autofit on ordinary list pages: template slides carry stale `a:normAutofit fontScale` values (e.g. 77500 → 77.5%) that would shrink the generated text when the deck is opened in PowerPoint. Replace them with a bare `a:normAutofit` (no attributes) so the written point sizes render at 100% and shrink only if content genuinely overflows. Cover and digest slides manage their own autofit explicitly and are exempt.

When a user-saved MHTML contains a new or renamed column, retain the item and use the deterministic fallback: Nature uses a standard summary slide when `display_summary=true` and otherwise a title-list slide; Science uses its generic title-list slide. Template-role recognition improves fidelity for known columns but never controls inclusion.

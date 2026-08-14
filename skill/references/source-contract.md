# Per-Journal MHTML Source Contract

## Contents

1. Authority
2. Nature discovery and fields
3. Science TOC discovery and fields
4. Science digest fields
5. Inclusion and fallback rules
6. Normalized JSON

## 1. Authority

Each run handles one journal. The only downstream source is the user-saved MHTML set for that journal:

- Nature: one current-issue TOC MHTML.
- Science: the current-issue TOC plus the `In Science Journals` and `In Other Journals` detail-page MHTMLs.

`issue.json` must record each supplied file's absolute path, SHA-256 hash, embedded source URL, and `user-manual-save` mode. The agent must not browse, automate, or download these pages.

Do not assume that a previous issue's columns still exist. Do not filter the current issue through an old fixed section allowlist.

## 2. Nature discovery and fields

Read issue metadata from the issue `h1`. Discover the ordered section inventory from every `.app-toc__item a[href]` fragment. For each fragment, require a matching `section[id]` and read all `.c-card` entries in page order. If the navigation is absent, fall back to all `section[id]` elements containing `.c-card`; record the discovered order.

Before the user saves the MHTML, instruct them to expand `.app-promo-text__button` until it shows `show less` (or the equivalent expanded state). Treat the saved MHTML as authoritative; the parser does not guarantee that it can prove the prior browser control state. Read cover title, `p[data-promo-text]`, credit, cover-article DOI URL, and embedded cover resource. If the parsed description is missing or clearly truncated, ask the user to save the page again.

Discover in-section groups from the actual TOC DOM, never from a fixed name list. A section's top-level `ul.app-article-list-row` holds `li` elements: a `li` containing `h3.c-section-heading` (typically `c-section-heading--no-bt`) is a group container whose `h3` text is the group name and whose nested `ul.app-article-list-row` cards belong to that group; a bare `li` holding cards directly has no group name. Sections without any group container, and runs of bare-card `li` elements, collapse into one implicit group named after the section. Record `sections[].groups` as an ordered `[{"name": group name, "items": [cards]}]`; keep `sections[].items` as a flattened list in the same order and stamp each card with its `group` name. Group names are the `h3` texts verbatim (for example `Features`, `Articles`, `Research Highlights`) and are not normalized to card types, which may differ (`Features` vs `News Feature`, `Articles` vs `Article`, `News` containing a single `News Q&A` card).

For every card read type, title, summary, authors, canonical URL, and DOI. A title or URL is mandatory. Empty summaries are valid.

Known title-only Nature types are Book Review, Correspondence, Career Column, Career Feature, Technology Feature, Correction, Author Correction, Publisher Correction, and Retraction Note. For any other type, retain and translate a nonempty card summary. This completeness-first fallback prevents a new type from losing its text.

## 3. Science TOC discovery and fields

Read issue metadata and cover from the user-saved TOC MHTML. Before the user saves it, instruct them to expand `.journal-issue__details .view-more` until it shows `View Less` (or the equivalent expanded state). `View Cover` is not the description control. Treat the saved MHTML as authoritative; if the parsed description is missing or clearly truncated, ask the user to save it again.

Discover every actual `section.toc__section` in page order. Do not require the historical six section names. Inside each section, treat each `h5.to-section` as the current subsection label and every `h3.article-title` as a primary card. Read title, URL, DOI, authors, date, pages, abstract excerpt, and `.card-footer` relationships. Each primary card's abstract excerpt renders as a bilingual summary line under its title on the list page; `Research Articles` excerpts are truncated TOC teasers and are excluded from that rendering.

Cards titled `In Science Journals` and `In Other Journals` are digest wrappers and are replaced by their user-saved detail-page items. Wrapper recognition strips all non-alphanumeric characters from the card title before matching the two canonical digest names, so renamed or reformatted headings still resolve. Do not exclude an entire subsection merely because its old name was `Research Highlights`. Attach every explicit footer `RELATED Perspective` and `RELATED Research Article` as a subordinate relationship (the TOC shows the pairing from both sides); do not promote unrelated Editorial or Letter relationships.

Science template roles are selected per subsection first and per section as a fallback: known subsection names (In Depth, Perspectives, Research Articles, Working Life, and so on) and known section names (Editorial, News, Commentary, Essays, Research, Reviews, Careers) select their established roles; a section and subsection that are both unknown use the generic title-list slide and remain included. The slide title is the section (column) name by default; each subsection name renders once as a bold white label before its first item in that column, and continuation pages omit it. Pages split when the estimated visual-line budget is exceeded.

## 4. Science digest fields

For every `section[id^=sec-]` read topic from `h2 .core-label`, remaining headline text, first role-paragraph author/editor, penultimate role-paragraph full introduction, last role-paragraph citation, and every DOI anchor including `see also`. Missing intro, citation, or DOI is fatal. Each detail item occupies one slide. The deck renders only the first citation entry: when a citation carries a second DOI (`see also` or a co-submitted journal article), the text after the first DOI is dropped and only that DOI stays linked.

## 5. Inclusion and fallback rules

- Include every primary card discovered in the user-saved MHTML inventory except the two digest wrappers.
- Include every digest item, qualifying related Perspective, and qualifying related Research Article.
- Keep source order and stable DOI/URL-based IDs.
- Nature template roles are selected per in-column group: known group names (and, as a fallback, the majority card type inside the group) select their established roles, including `News Q&A` → news-feature and `Obituary` → books/comment/correspondence/work.
- The slide title is the section (column) name by default; a template-level override may substitute the group name. Each group name renders once as a bold white label before its first item in that column; continuation pages omit it.
- New or renamed Nature groups use a standard summary slide when a summary exists, otherwise a title-list slide.
- New or renamed Science sections/subsections use the generic Science title-list slide.
- Science TOC cards with an abstract excerpt render it as a bilingual summary line under the title (all subsections except `Research Articles`, whose excerpts are truncated TOC teasers); cards without an excerpt render title-only.
- Ordinary slides split when the estimated visual-line budget is exceeded (see template-features §6; `MAX_PAGE_LINES` is 20 for Nature and 12 for Science). Nature pages also follow the density caps: `Articles` groups stay on dedicated pages with at most two primary items per page (allowing one on the final page); pages holding summarised items hold at most three; title-only pages hold at most four; summarised and title-only items never share a page. Each related Perspective or related Research Article line contributes two estimated visual lines on line-budgeted pages.
- Never use a changed column name as a reason to omit content.

## 6. Normalized JSON

The `scrape` stage (via `scripts/run_nature_only.py` or `scripts/run_science_only.py`) writes acquisition evidence, dynamic `nature.sections` and `science.sections/subsections`, plus covers, digests, related Perspectives, counts, stable IDs, and `display_summary`/`is_digest_wrapper` flags.

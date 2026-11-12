# Dynamic data, staff categories and themes

## Using the new controls

- In **Teaching staff**, choose a broad staff category, then optionally a specific rank. The breakdown chart can switch between broad categories and specific ranks. The same controls work for internal and external staff.
- In the student and graduate explorers, **Birth year** filters by the value recorded in the source. Unknown values and grouped years remain selectable; a range is not treated as an exact year.
- With a graduate birth-year filter active, the breakdown shows gender; clicking a segment filters that gender. Clearing the birth-year filter restores the degree-grade breakdown.
- **Light mode / Dark mode** changes the interface, chart labels and map palette. Dark mode remains the default and retains the existing design. The browser remembers your choice.
- **Refresh data** reloads metadata and the current explorer from the selected source, bypassing the five-minute cache. Select **Live database** when checking newly published records. Snapshot mode intentionally uses the bundled historical RDF files.

## Staff hierarchy

Broad categories and ranks come from the database's `skos:broader` relationships and English concept labels, using the hierarchy in `concepts_21.ttl`. They are not maintained as a second hardcoded list in the interface. DEP corresponds to Regular Teaching and Research Staff; its ranks currently include Professor, Associate Professor, Assistant Professor and Lecturer. Other branches include temporary, collaborating, laboratory, special, auxiliary and other teaching staff, where observations exist.

When both a parent total and descendant observations exist for the same department, academic year and gender, queries use the detailed observations and exclude the parent total to avoid counting it twice. Publish complete rank breakdowns when also publishing their parent total: a partial breakdown cannot safely be interpreted as the complete parent population. A category with ambiguous membership in multiple broad groups remains a specific category in the explorer rather than being counted twice; an AI request to group such ambiguous data asks for clarification.

## Birth-year coverage

| Explorer | Birth-year source | Limitation |
| --- | --- | --- |
| Undergraduate students | `studentsByBirthYearStudyYearDataset` (06), BSc | Unfiltered enrollment uses table 04; the populations in the supplied data differ. |
| Postgraduate students | `studentsByBirthYearDataset` (05), MSc/PhD | Keeps the corrected exclusion of BSc. |
| First-year students | Table 06, BSc and StudyYear1 | Birth year can be combined with the first-year restriction. |
| Graduates | `graduatesByBirthYearDataset` (09), BSc | Grade data are in a separate aggregate table (08), so grade/excellent-share and birth-year filters cannot be combined. |
| Teaching staff | No birth-year dimension supplied | No birth-year control is shown. |

For example, the current 2023–2024 undergraduate total is 58,466 in table 04, while table 06 contains 57,611 across its recorded birth-year cells. Selecting a birth year switches to table 06; the interface's methodology note explains the source change. Values such as `1951 KAI PRIN` and `AGNOSTO` are displayed as “1951 or earlier” and “Unknown birth year.” No individual birth years are inferred from grouped values.

## Adding data in the future

For new records that follow the existing UniStats RDF schema in the configured named graphs, no frontend list needs editing:

1. Publish the new observations to the existing database using the existing dataset URI and dimension predicates: academic year, program or department, gender, count, and the dataset's category/birth-year dimension.
2. For a new department, publish its `uni:Department` type, labels, university → school → department relationships and program/type relationships. Optional `geo:lat` and `geo:long` allow it to appear on the map.
3. For a new staff rank, add its English label and `skos:broader` relationship. Its observations make it available as a specific filter; its ancestry determines its broad category.
4. In the app, select **Live database** and click **Refresh data**. Years, institutions, categories, birth-year values and map locations are rediscovered. The catalogue's year choices also update. The AI refreshes its live vocabulary when the metadata revision changes.

Metadata and ordinary results are cached for up to five minutes. The refresh button bypasses both caches for the explorer. AI statistics always execute a fresh live SELECT; AI metadata may use the five-minute live metadata cache. Refreshing data from the AI page updates that live metadata immediately. Failed live AI requests never substitute snapshot answers.

Live coordinates take priority over the workbook fallback. Snapshot mode preserves the exact workbook coordinates. `data/website-links.json` supplies confirmed website redirect overrides, not an institution allowlist; new institutions retain valid HTTP(S) RDF identifiers as links.

This supports new records within the existing schema. A new statistical cube, new dimension or different ontology requires a backend mapping. Graph names are configured in `backend/config.py`. Historical cohort definitions in the approved thesis catalogue remain unchanged. Large explorer responses still have the existing 25,000-row safety limit; exceeding it produces an explicit error rather than silently truncating results.

## Verification

`tests/test_dynamic_data.py` adds a future year, university, department, map coordinate, staff rank, birth-year value and duplicate aggregate totals to an isolated in-memory RDF store. It verifies discovery, filtering, correct totals and automatic AI vocabulary refresh. Those fixtures are removed after the test and are never written to the live database. `tests/dynamic.spec.js` checks the controls, refresh behavior, chart colors, theme persistence and phone layout.

See [AI_VALIDATION.md](AI_VALIDATION.md) for the latest test results and [API.md](API.md) for the new request parameters.

# HTTP API

Base URL for local development: `http://127.0.0.1:8000`. Responses are JSON. No authentication is required. The OpenAPI schema is available at `/openapi.json`; documentation assets from external CDNs are not loaded by the app.

## GET /api/health

Returns application health, whether the local store is ready, AI activation and the last observed remote connection state (`not_checked`, `connected`, or `unavailable`). This is not a fresh remote health probe.

## GET /api/metadata

Accepts `source=auto|live|snapshot` and `refresh=true` to bypass the five-minute cache. Returns discovered departments, institutions, programs, `concepts` with SKOS parent relationships, `staffGroups`, per-dataset `vocabulary` (years, categories, recorded birth-year values and birth-table years), `availableYears`, supported datasets and metrics. `source` identifies the actual source; `revision` changes when the bundle changes. Auto fallback uses a complete snapshot bundle, never mixed live/snapshot metadata.

`institutions` maps RDF identifiers to English labels, kinds and website URLs. `data/website-links.json` provides redirect overrides; new institutions are discovered without editing that file. Department `location` contains latitude/longitude or null. Live RDF coordinates take priority in live mode, with the workbook snapshot as fallback. Snapshot mode preserves the imported workbook coordinates. `locationSource` records workbook provenance; individual RDF coordinates carry their own source label.

## GET /api/explore

| Parameter | Allowed values / meaning |
| --- | --- |
| `dataset` | `students` (default), `postgraduates`, `graduates`, `staff`, `external`, `entry` |
| `source` | `auto` (default), `live`, `snapshot` |
| `year` | An available academic-year identifier, e.g. `AY2023_2024`; default latest |
| `university`, `department`, `region`, `city` | Exact RDF URI from metadata; optional |
| `gender` | `Female` or `Male`; omit for both |
| `level` | `MSc` or `PhD`, for postgraduates |
| `category` | Dataset category identifier, e.g. `BeyondNormalYears` or `Excellent` |
| `staff_group` | Broad teaching-staff identifier from metadata; may combine with a specific `category` |
| `category_detail` | `broad` (default) or `specific`, for the staff breakdown chart |
| `birth_year` | Exact recorded literal from dataset vocabulary, including grouped/unknown values |
| `refresh` | `true` bypasses result and metadata caches |
| `metric` | `total`, `femaleShare`, `beyondShare` (students), `excellentShare` (graduates) |
| `group_by` | `department` (default), `university`, `region`, `city` |
| `top` | Number of chart groups, from 3 to 500; default 10 |

Returns `summary`, `ranking` (top N), `allRows` (all groups), `trend`, `distribution`, available years/categories/birth years/staff groups, the metadata bundle, methodology note, source metadata, SPARQL text and an explanation of API-side filtering. Counts are numbers, percentages are on a 0–100 scale. Empty selections have `observedGroups: 0` and null shares; clients should present missing observations, not a measured zero. Missing trend values are null.

Birth-year filtering selects table 06 for undergraduate/entry students, table 05 for postgraduates and table 09 for graduates. Staff have no birth-year data. Graduate grade/excellent-share filters cannot be combined with birth-year filtering because they belong to separate aggregate tables. Unsupported combinations return 422. See [DYNAMIC_DATA.md](DYNAMIC_DATA.md) for coverage details.

Example:

```text
/api/explore?dataset=students&year=AY2023_2024&gender=Female&group_by=university&source=snapshot
```

## GET /api/catalogue

Returns `queries`, an array with ID, title, group, supported filters and corrected SPARQL text.

## POST /api/catalogue/{query_id}/run

```json
{
  "source": "auto",
  "filters": {
    "academicYear": "AY2023_2024",
    "gender": "Female",
    "programType": "MSc"
  }
}
```

Only supply filters listed by that query's catalogue entry. Supported fields are `academicYear`, `department`, `gender` and `programType`. Department values must match the metadata allowlist. Study-level values are restricted to MSc or PhD. Omit a filter for all values within the query's approved scope. Unsupported filter fields are rejected.

The response contains `columns`, `rows`, `totalRows`, `queryId`, executed `query` and `source`. RDF bindings are strings and missing bindings are absent keys. Large remote results are fetched in pages; the browser receives the complete bounded result. There is no arbitrary-SPARQL or update endpoint.

## GET /api/ai/status and POST /api/ai/query

Status returns `enabled`, `ready`, `busy`, `provider: "openai_compatible"`, `model`, `executionSource: "live"` and a setup/readiness `message`. `ready` means that AI is enabled and the required configuration is syntactically valid. Status makes no provider/database request, does not validate credentials, and returns `connectionVerified: false`. The first question tests the connection. Credentials and the base URL are never returned. Missing or invalid configuration leaves the rest of the app available.

Post an English question of 3–2,000 characters:

```json
{"question":"Which 5 universities have the most undergraduate students in 2023-2024?"}
```

Optional `previous_question` (up to 2,000 characters) supplies context for an explicit follow-up. Unknown fields, including `source` or raw `query`, are rejected. No questions are persisted by the application.

The backend asks the hosted model for a structured plan, validates it, compiles a server-owned SELECT query, and calls `Database.query(query, "live", fresh=True)`. It never substitutes cached or snapshot results. Before planning, it loads live institution/coverage/hierarchy vocabulary from the metadata store (cached for up to five minutes) and rebuilds the planner context when its revision changes. There is no snapshot fallback for this live vocabulary. Staff-group and birth-year filters/groupings use this discovered vocabulary.

A successful response has `status: "answer"`, `executed: true`, `kind`, `answer`, `interpretation`, `assumptions`, `note`, `plan`, `query`, `columns`, `rows`, `totalRows`, `unit`, `years`, `source`, `provider`, `model`, `requestId`, `planRevisions` (0 or 1) and `timing` (`planningMs`, `totalMs`). Numeric answer text is computed from returned rows, not generated by the model. RDF values in rows are strings. A statistics row without `value` means an undefined percentage, not zero.

Unsupported or ambiguous plans return HTTP 200 with `status: "clarification"`, `message` and `executed: false`. No query is executed in this case. A detected mismatch with an explicit question constraint permits one model revision; the response reports its count. Missing configuration, invalid/unavailable model responses, authentication failures, provider rate/usage limits and live database failures return 503 with a sanitized message. Provider error bodies are not forwarded. Transport errors are not automatically retried; at most one semantic correction is allowed. The selected model and provider must support the configured JSON output format and token-limit parameter. Only one AI request per application process runs at a time; additional requests return 429 with `Retry-After: 10`. The total planning deadline, including a possible revision, defaults to 90 seconds, followed by the database's existing 35-second bound.

## Errors and limits

- 422: invalid parameters, unsupported filters, or an unavailable academic year.
- 503: live database unavailable, incomplete result, or a result exceeding configured limits. Auto mode can return a labelled local snapshot when the remote call fails.
- 429: request limit reached; respect the `Retry-After` header.
- 413: POST body exceeds 16 KiB. POST requests must carry Content-Length; requests without it return 411.

Explorer/catalogue results are cached for up to five minutes. `source.cached` indicates reuse and `source.retrievedAt` retains the actual retrieval time. Changing filters in an explorer can reuse the same cached cube while producing a new filtered response. AI statistical requests always bypass this result cache; live metadata has its separate five-minute cache.

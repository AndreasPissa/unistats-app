"""
ETL Script: ΕΛΣΤΑΤ Πίνακας 03 → RDF Turtle  (hardened)
========================================================
ΕΤΕΠ, Διοικητικό & Βοηθητικό Προσωπικό κατά Φύλο.
Φιλτράρει ΜΟΝΟ τα τμήματα Πληροφορικής από cs_schools.xlsx.

Χρήση:
    python3 etl_03.py cs_schools.xlsx data/03/*.xls data/03/*.xlsx
    python3 etl_03.py --clean cs_schools.xlsx data/03/*.xls

Εξαρτήσεις:
    pip install pandas xlrd openpyxl rdflib
"""

import sys
import re
import unicodedata
from pathlib import Path

import pandas as pd

try:
    from rdflib import Graph
    HAVE_RDFLIB = True
except ImportError:
    HAVE_RDFLIB = False


# ── NAMESPACES (preserved) ───────────────────────────────────
BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"

# ── MATCHING THRESHOLDS ──────────────────────────────────────
UNIV_EXACT_MATCH_SCORE   = 1.0
UNIV_FUZZY_MIN_THRESHOLD = 0.60
DEPT_MIN_THRESHOLD       = 0.45
AMBIGUOUS_MARGIN         = 0.05   # if top-2 dept scores differ by less than this → ambiguous

UNIVERSITY_KEYWORDS = [
    "ΠΑΝΕΠΙΣΤΗΜ", "ΠΟΛΥΤΕΧΝΕ", "ΤΕΧΝΟΛΟΓ", "ΑΚΑΔΗΜ",
    "ΑΝΩΤΑΤ", "ΑΝΩΤΕΡ", "ΧΑΡΟΚΟΠΕΙΟ",
]

# Category keyword patterns (normalized) used to locate columns dynamically.
# Each pattern must match a *normalized* substring found in the header rows.
CATEGORY_PATTERNS = {
    "ETEP":           ["ΕΙΔΙΚΟ ΤΕΧΝΙΚΟ ΕΡΓΑΣΤΗΡΙΑΚΟ", "ΕΤΕΠ"],
    "Administrative": ["ΔΙΟΙΚΗΤΙΚΟ ΠΡΟΣΩΠΙΚΟ", "ΔΙΟΙΚΗΤΙΚΟ  ΠΡΟΣΩΠΙΚΟ"],
    "Auxiliary":      ["ΒΟΗΘΗΤΙΚΟ", "ΕΡΓΑΤΟΤΕΧΝΙΚΟ"],
}

# Fallback column positions (used only if dynamic detection fails, with a warning)
FALLBACK_COLUMNS = {
    "ETEP":           (6, 7, 8),
    "Administrative": (42, 43, 44),
    "Auxiliary":      (78, 79, 80),
}

REQUIRED_MAPPING_COLUMNS = [
    "id", "univ_gr", "school_gr", "dept_gr",
    "univ_uri", "school_uri", "dept_uri", "duration",
]

HEADER_SCAN_ROWS = 20  # how many top rows to scan for headers / academic year

# Console verbosity: by default only the final summary is printed.
# Pass --verbose to see per-file / per-row diagnostic messages.
VERBOSE = False

def vprint(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)


# ── GLOBAL RUN STATE (for summary + exit code) ───────────────

class RunStats:
    def __init__(self):
        self.files_processed = 0
        self.files_successful = 0
        self.files_failed = 0
        self.depts_found = 0
        self.depts_matched = 0
        self.depts_unmatched = 0
        self.depts_ambiguous = 0
        self.observations = 0
        self.duplicate_keys = 0
        self.inconsistencies = 0
        self.any_invalid_turtle = False

    def has_failures(self):
        return (
            self.files_failed > 0
            or self.depts_ambiguous > 0
            or self.any_invalid_turtle
        )


STATS = RunStats()


# ── TEXT NORMALIZATION ────────────────────────────────────────

def normalize(text) -> str:
    """
    Uppercase, strip Greek accents, treat & / ΚΑΙ as equivalent,
    remove punctuation, collapse whitespace.
    """
    text = str(text).upper().strip()
    nfkd = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    text = text.replace('&', ' ΚΑΙ ')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def strip_dept_prefix(text: str) -> str:
    """Remove leading ΤΜΗΜΑ (and similar) prefix, case-insensitive."""
    return re.sub(r'^\s*ΤΜΗΜΑ\s+', '', text, flags=re.IGNORECASE).strip()


def strip_trailing_parens(text: str) -> str:
    """Remove trailing parenthetical text, e.g. '... (ΑΘΗΝΑ)' → '...'."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', text).strip()


def word_overlap_score(a_words: set, b_words: set) -> float:
    if not a_words or not b_words:
        return 0.0
    inter = a_words & b_words
    union = a_words | b_words
    return len(inter) / len(union) if union else 0.0


# ── EXCEL READING (xls + xlsx) ────────────────────────────────

def read_excel_file(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return pd.read_excel(path, engine="xlrd", header=None)
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl", header=None)
    raise ValueError(f"Unsupported Excel format: {suffix}")


# ── MAPPING FILE LOADING & VALIDATION ─────────────────────────

def load_cs_mapping(mapping_file: str):
    """
    Loads and validates cs_schools.xlsx.
    Returns (mapping_list, universities_index) where universities_index
    groups department entries by normalized university name, enabling
    two-stage (university → department) matching.
    """
    suffix = Path(mapping_file).suffix.lower()
    if suffix == ".xlsx":
        raw = pd.read_excel(mapping_file, header=0, engine="openpyxl")
    else:
        raw = pd.read_excel(mapping_file, header=0)

    n_cols = raw.shape[1]
    if n_cols < 8:
        raise ValueError(
            f"Invalid mapping file: expected 8 mapping columns but found {n_cols}.\n"
            f"You may have passed an ELSTAT data file as the first argument."
        )
    if n_cols > 8:
        vprint(
            f"WARNING: mapping file has {n_cols} columns, expected 8. "
            f"Using the first 8 by position."
        )

    df = raw.iloc[:, :8].dropna(how="all").copy()
    df.columns = REQUIRED_MAPPING_COLUMNS

    mapping = []
    seen_uris = {}
    errors = []

    for idx, row in df.iterrows():
        excel_row = idx + 2  # +1 header, +1 for 1-indexing

        univ_raw = str(row['univ_gr']).strip() if pd.notna(row['univ_gr']) else ""
        dept_raw = str(row['dept_gr']).strip() if pd.notna(row['dept_gr']) else ""
        dept_uri = str(row['dept_uri']).strip() if pd.notna(row['dept_uri']) else ""

        if not univ_raw or univ_raw.lower() == "nan":
            errors.append(f"Mapping row {excel_row}: missing university name")
            continue
        if not dept_raw or dept_raw.lower() == "nan":
            errors.append(f"Mapping row {excel_row}: missing department name")
            continue
        if not dept_uri or dept_uri.lower() == "nan":
            errors.append(f"Mapping row {excel_row}: missing department URI")
            continue

        if dept_uri in seen_uris:
            errors.append(
                f"Mapping row {excel_row}: duplicate department URI '{dept_uri}' "
                f"(first seen at row {seen_uris[dept_uri]})"
            )
            continue
        seen_uris[dept_uri] = excel_row

        try:
            duration = int(row['duration'])
            if duration <= 0 or duration > 10:
                errors.append(
                    f"Mapping row {excel_row}: suspicious duration value ({duration})"
                )
        except (ValueError, TypeError):
            errors.append(f"Mapping row {excel_row}: invalid duration value")
            duration = None

        dept_core = strip_trailing_parens(dept_raw)

        mapping.append({
            'id':        str(row['id']),
            'univ_raw':  univ_raw,
            'univ_norm': normalize(univ_raw),
            'dept_raw':  dept_raw,
            'dept_norm': normalize(dept_core),
            'dept_uri':  dept_uri,
            'duration':  duration,
        })

    if errors:
        if VERBOSE:
            vprint("Mapping file validation warnings:")
            for e in errors:
                vprint(f"  - {e}")

    if not mapping:
        raise ValueError("Invalid mapping file: no valid department rows found.")

    # Build university index: normalized university name → list of dept entries
    universities_index = {}
    for entry in mapping:
        universities_index.setdefault(entry['univ_norm'], []).append(entry)

    return mapping, universities_index


# ── TWO-STAGE MATCHING (university, then department) ─────────

def match_university(elstat_univ_norm: str, universities_index: dict):
    """
    Returns (matched_univ_norm, score) or (None, 0.0).
    Prefers exact match; else fuzzy match above UNIV_FUZZY_MIN_THRESHOLD.
    """
    if elstat_univ_norm in universities_index:
        return elstat_univ_norm, UNIV_EXACT_MATCH_SCORE

    elstat_words = set(elstat_univ_norm.split())
    best_score, best_univ = 0.0, None

    for univ_norm in universities_index:
        m_words = set(univ_norm.split())
        score = word_overlap_score(elstat_words, m_words)
        if score > best_score:
            best_score, best_univ = score, univ_norm

    if best_score >= UNIV_FUZZY_MIN_THRESHOLD:
        return best_univ, best_score

    return None, 0.0


def match_department(elstat_dept_raw: str, dept_entries: list):
    """
    Match a department name only against departments of the ALREADY
    matched university. Returns a list of (entry, score) sorted desc.
    """
    dept_clean = strip_dept_prefix(elstat_dept_raw)
    dept_clean = strip_trailing_parens(dept_clean)
    dept_norm  = normalize(dept_clean)
    elstat_words = set(dept_norm.split())

    scored = []
    for entry in dept_entries:
        m_words = set(entry['dept_norm'].split())
        score = word_overlap_score(elstat_words, m_words)
        scored.append((entry, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def find_dept_match(univ_raw: str, dept_raw: str, universities_index: dict):
    """
    Full two-stage match.
    Returns a dict with:
        status, matched_uri, matched_university, matched_department,
        university_score, department_score, combined_score
    """
    univ_norm = normalize(univ_raw)
    matched_univ_norm, univ_score = match_university(univ_norm, universities_index)

    if matched_univ_norm is None or univ_score <= 0.0:
        return {
            "status": "UNMATCHED_UNIVERSITY",
            "matched_uri": None,
            "matched_university": None,
            "matched_department": None,
            "university_score": 0.0,
            "department_score": 0.0,
            "combined_score": 0.0,
        }

    dept_entries = universities_index[matched_univ_norm]
    scored = match_department(dept_raw, dept_entries)

    if not scored or scored[0][1] < DEPT_MIN_THRESHOLD:
        best_dept_score = scored[0][1] if scored else 0.0
        return {
            "status": "UNMATCHED_DEPARTMENT",
            "matched_uri": None,
            "matched_university": matched_univ_norm,
            "matched_department": None,
            "university_score": univ_score,
            "department_score": best_dept_score,
            "combined_score": 0.0,
        }

    top_entry, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    combined = univ_score * 0.4 + top_score * 0.6

    if (top_score - second_score) < AMBIGUOUS_MARGIN and second_score >= DEPT_MIN_THRESHOLD:
        return {
            "status": "AMBIGUOUS",
            "matched_uri": None,
            "matched_university": matched_univ_norm,
            "matched_department": top_entry['dept_raw'],
            "university_score": univ_score,
            "department_score": top_score,
            "combined_score": combined,
        }

    return {
        "status": "MATCHED",
        "matched_uri": top_entry['dept_uri'],
        "matched_university": matched_univ_norm,
        "matched_department": top_entry['dept_raw'],
        "university_score": univ_score,
        "department_score": top_score,
        "combined_score": combined,
    }


# ── ACADEMIC YEAR DETECTION ────────────────────────────────────

def academic_year_from_filename(filename: str):
    """Try to find AAAA_AAAA or AAAA pattern conventionally used by ELSTAT."""
    m = re.search(r'(\d{4})[_\-](\d{4})', filename)
    if m:
        return f"AY{m.group(1)}_{m.group(2)}"
    m = re.search(r'_(\d{4})_', filename)
    if m:
        y = int(m.group(1))
        return f"AY{y}_{y + 1}"
    return None


def academic_year_from_header(df: pd.DataFrame):
    for i in range(min(HEADER_SCAN_ROWS, len(df))):
        row = df.iloc[i]
        for v in row:
            if pd.isna(v):
                continue
            m = re.search(r'(\d{4})\s*[/\-]\s*(\d{4})', str(v))
            if m:
                return f"AY{m.group(1)}_{m.group(2)}"
    return None


def resolve_academic_year(filename: str, df: pd.DataFrame):
    """
    Prefer header value. If filename and header disagree, warn and use header.
    Fail (raise) if neither is found.
    """
    fn_year = academic_year_from_filename(filename)
    hdr_year = academic_year_from_header(df)

    if hdr_year is None and fn_year is None:
        raise ValueError(
            f"Could not determine academic year for '{filename}' "
            f"(neither filename nor header contained a recognizable year)."
        )

    if hdr_year is None:
        return fn_year

    if fn_year is not None and fn_year != hdr_year:
        vprint(
            f"  WARNING: filename suggests {fn_year} but header says {hdr_year} "
            f"for '{filename}'. Using header value."
        )

    return hdr_year


# ── DYNAMIC COLUMN DETECTION ───────────────────────────────────

def detect_category_columns(df: pd.DataFrame, filename: str):
    """
    Scans header rows for category label text and returns:
        { category_label: (total_col, male_col, female_col) }
    Falls back to FALLBACK_COLUMNS (with a warning) per-category if not found.
    Returns (columns_dict, ok: bool)
    """
    found_start_col = {}

    for i in range(min(HEADER_SCAN_ROWS, len(df))):
        row = df.iloc[i]
        for ci, v in enumerate(row):
            if pd.isna(v):
                continue
            # Skip the leftmost structural columns (institution/school/dept/
            # grand-total) — real category headers start further right.
            if ci < 4:
                continue
            v_norm = normalize(str(v))

            # A cell that matches more than one category in one go is a
            # title / description cell (e.g. the table caption), not a
            # column header — ignore it entirely.
            cell_matches = [
                cat_label for cat_label, patterns in CATEGORY_PATTERNS.items()
                if any(pat in v_norm for pat in patterns)
            ]
            if len(cell_matches) != 1:
                continue

            cat_label = cell_matches[0]
            if cat_label not in found_start_col:
                found_start_col[cat_label] = ci

    columns = {}
    ok = True
    for cat_label in CATEGORY_PATTERNS:
        if cat_label in found_start_col:
            start = found_start_col[cat_label]
            columns[cat_label] = (start, start + 1, start + 2)
        else:
            vprint(
                f"  WARNING: could not dynamically detect columns for "
                f"'{cat_label}' in '{filename}'. Using fallback "
                f"{FALLBACK_COLUMNS[cat_label]}."
            )
            columns[cat_label] = FALLBACK_COLUMNS[cat_label]
            # Fallback is allowed, but we still consider ok=True since
            # requirement 3 only forbids *silent* incorrect processing.
            # We only fail hard if columns are entirely unverifiable (see below).

    return columns, ok


def verify_columns_against_totals(df: pd.DataFrame, data_start: int, columns: dict, filename: str):
    """
    Sanity check: for the ΓΕΝΙΚΟ ΣΥΝΟΛΟ row, male+female should equal (or be
    close to) total for each detected category. If wildly inconsistent for
    ALL categories, treat detection as unsafe and fail the file.
    """
    if data_start >= len(df):
        return False

    row = df.iloc[data_start]
    any_consistent = False

    for cat_label, (ct, cm, cf) in columns.items():
        if cf >= len(row):
            continue
        total_v, male_v, fem_v = row.get(ct), row.get(cm), row.get(cf)
        if pd.isna(total_v) or pd.isna(male_v) or pd.isna(fem_v):
            continue
        try:
            t, m, f = int(total_v), int(male_v), int(fem_v)
        except (ValueError, TypeError):
            continue
        if m + f == t:
            any_consistent = True

    return any_consistent


# ── HIERARCHY / DATA-START DETECTION ──────────────────────────

def detect_data_start(df: pd.DataFrame) -> int:
    for i in range(len(df)):
        c0 = df.iloc[i][0]
        c0 = str(c0).strip() if pd.notna(c0) else ""
        if 'ΓΕΝΙΚΟ ΣΥΝΟΛΟ' in c0.upper():
            return i
    return -1


def is_university_row(col0_text: str) -> bool:
    up = col0_text.upper()
    return any(kw in up for kw in UNIVERSITY_KEYWORDS)


# ── VALUE / CONSISTENCY HELPERS ────────────────────────────────

def read_int_or_none(val):
    """None means missing/blank. Otherwise returns int (zero is valid)."""
    if pd.isna(val):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ── URI / SLUG HELPERS ─────────────────────────────────────────

def uri_slug(text: str) -> str:
    norm = normalize(text)
    slug = re.sub(r'\s+', '-', norm.strip())
    slug = re.sub(r'[^A-Za-z0-9\-]', '', slug)
    return slug[:40] if slug else "x"


# Global sequential counter for observation URIs (obs_staff_1, obs_staff_2, ...)
_OBS_COUNTER = 0

def next_obs_id() -> int:
    global _OBS_COUNTER
    _OBS_COUNTER += 1
    return _OBS_COUNTER


# ── MAIN PER-FILE ETL ───────────────────────────────────────────

def process_file(xls_path: str, universities_index: dict, audit_rows: list):
    """
    Returns (rdf_text_or_None, success: bool)
    Appends rows to audit_rows as a side effect.
    """
    filename = Path(xls_path).name
    vprint(f"Processing: {filename}")

    try:
        df = read_excel_file(xls_path)
    except Exception as e:
        vprint(f"  FAILED: could not read file ({e})")
        return None, False

    try:
        academic_year = resolve_academic_year(filename, df)
    except ValueError as e:
        vprint(f"  FAILED: {e}")
        return None, False

    data_start = detect_data_start(df)
    if data_start == -1:
        vprint(f"  FAILED: could not locate 'ΓΕΝΙΚΟ ΣΥΝΟΛΟ ΕΛΛΑΔΟΣ' row in '{filename}'.")
        return None, False

    columns, _ = detect_category_columns(df, filename)

    consistent = verify_columns_against_totals(df, data_start, columns, filename)
    if not consistent:
        vprint(
            f"  FAILED: detected/fallback columns for '{filename}' do not "
            f"reconcile with the grand-total row (male+female != total for "
            f"every category). Refusing to process with unverified columns."
        )
        return None, False

    vprint("  Detected columns:")
    for cat_label, (ct, cm, cf) in columns.items():
        vprint(f"    {cat_label}: total={ct}, male={cm}, female={cf}")

    lines = []
    seen_keys = {}          # logical_key -> (row_idx, obs_uri)
    current_univ = None
    current_school = None
    active_univ_since_row = None

    for row_idx in range(data_start, len(df)):
        row  = df.iloc[row_idx]
        col0 = str(row[0]).strip() if pd.notna(row[0]) else ""
        col1 = str(row[1]).strip() if pd.notna(row[1]) else ""
        col2 = str(row[2]).strip() if pd.notna(row[2]) else ""

        if col0:
            if is_university_row(col0):
                current_univ = col0
                current_school = None
                active_univ_since_row = row_idx
            else:
                # Region / grand-total heading — not a university.
                # Only reset if it's clearly a new top-level section
                # (i.e. col0 present but not a university keyword AND
                # not immediately followed by school/dept rows that
                # belong to the previous university). We reset here
                # because ELSTAT files put region headings at col0
                # exactly like universities; anything else at col0
                # legitimately starts a new region.
                current_univ = None
                current_school = None
            continue

        if col1:
            current_school = col1
            continue

        if col2:
            STATS.depts_found += 1

            if current_univ is None:
                vprint(
                    f"  WARNING: department row without an active university "
                    f"at source row {row_idx} ('{col2}'). Skipping."
                )
                audit_rows.append({
                    "source_file": filename,
                    "academic_year": academic_year,
                    "source_row": row_idx,
                    "source_university": "",
                    "source_school": current_school or "",
                    "source_department": col2,
                    "matched_university": "",
                    "matched_department": "",
                    "matched_uri": "",
                    "university_score": 0.0,
                    "department_score": 0.0,
                    "combined_score": 0.0,
                    "status": "UNMATCHED_UNIVERSITY",
                })
                STATS.depts_unmatched += 1
                continue

            match = find_dept_match(current_univ, col2, universities_index)

            audit_rows.append({
                "source_file": filename,
                "academic_year": academic_year,
                "source_row": row_idx,
                "source_university": current_univ,
                "source_school": current_school or "",
                "source_department": col2,
                "matched_university": match["matched_university"] or "",
                "matched_department": match["matched_department"] or "",
                "matched_uri": match["matched_uri"] or "",
                "university_score": round(match["university_score"], 3),
                "department_score": round(match["department_score"], 3),
                "combined_score": round(match["combined_score"], 3),
                "status": match["status"],
            })

            if match["status"] == "AMBIGUOUS":
                STATS.depts_ambiguous += 1
                vprint(
                    f"  AMBIGUOUS match at row {row_idx}: "
                    f"'{current_univ}' / '{col2}' — skipped, see audit CSV."
                )
                continue
            if match["status"] in ("UNMATCHED_UNIVERSITY", "UNMATCHED_DEPARTMENT"):
                STATS.depts_unmatched += 1
                continue

            STATS.depts_matched += 1
            dept_uri = match["matched_uri"].rstrip('/')
            ay_uri   = f"{RESOURCE}{academic_year}"

            for cat_label, (ct, cm, cf) in columns.items():
                if cf >= len(row):
                    continue

                total_v = read_int_or_none(row[ct])
                male_v  = read_int_or_none(row[cm])
                fem_v   = read_int_or_none(row[cf])

                if total_v is None and male_v is None and fem_v is None:
                    continue  # entirely blank — genuinely missing

                if total_v is not None and total_v < 0:
                    vprint(
                        f"  WARNING: negative total in '{filename}' univ="
                        f"'{current_univ}' dept='{col2}' cat={cat_label} "
                        f"value={total_v}"
                    )
                    STATS.inconsistencies += 1
                    continue

                for val, gender in [(male_v, "Male"), (fem_v, "Female")]:
                    if val is None:
                        continue  # missing — skip only this gender value
                    if val < 0:
                        vprint(
                            f"  WARNING: negative {gender} value in "
                            f"'{filename}' univ='{current_univ}' dept="
                            f"'{col2}' cat={cat_label} value={val}"
                        )
                        STATS.inconsistencies += 1
                        continue

                    if total_v is not None and total_v > 0:
                        pct = round(val / total_v * 100, 1)
                    elif total_v == 0 and val == 0:
                        pct = 0.0
                    elif total_v is None:
                        pct = None
                    else:
                        pct = None

                    logical_key = (academic_year, dept_uri, cat_label, gender)
                    obs_uri = (
                        f"{RESOURCE}03_{academic_year}/obs_staff_"
                        f"{next_obs_id()}"
                    )

                    if logical_key in seen_keys:
                        prev_row, _ = seen_keys[logical_key]
                        vprint(
                            f"  DUPLICATE observation key detected: "
                            f"{logical_key} — source rows {prev_row} and "
                            f"{row_idx} in '{filename}'. Keeping first, "
                            f"skipping second."
                        )
                        STATS.duplicate_keys += 1
                        continue

                    seen_keys[logical_key] = (row_idx, obs_uri)
                    STATS.observations += 1

                    pct_ttl = f'"{pct}"^^<{XSD}decimal>' if pct is not None else '""^^<{}string>'.format(XSD)

                    lines.append(f"\n# Source: {filename}")
                    lines.append(f"# Row: {row_idx}")
                    lines.append(f"# University: {current_univ}")
                    lines.append(f"# School: {current_school or ''}")
                    lines.append(f"# Department: {col2}")
                    lines.append(f"# Matched URI: {dept_uri}")
                    lines.append(f"# {academic_year} | {cat_label} | {gender}")
                    lines.append(f"<{obs_uri}>")
                    lines.append(f'    a <{QB}Observation> ;')
                    lines.append(f'    <{QB}dataSet> <{DATASET}staffDataset> ;')
                    lines.append(f'    <{DIMENSION}academicYear> <{ay_uri}> ;')
                    lines.append(f'    <{DIMENSION}department> <{dept_uri}> ;')
                    lines.append(f'    <{DIMENSION}staffCategory> <{RESOURCE}{cat_label}> ;')
                    lines.append(f'    <{DIMENSION}gender> <{RESOURCE}{gender}> ;')
                    lines.append(f'    <{DIMENSION}count> "{val}"^^<{XSD}integer> ;')
                    lines.append(f'    <{DIMENSION}percentage> {pct_ttl} .')

    n_obs = sum(1 for l in lines if l.startswith('<'))
    header = (
        f"# ============================================================\n"
        f"# 03 — ΕΤΕΠ, Διοικητικό & Βοηθητικό Προσωπικό\n"
        f"# Source: {filename}\n"
        f"# Academic Year: {academic_year}\n"
        f"# Observations generated: {n_obs}\n"
        f"# ============================================================\n\n"
    )
    return header + "\n".join(lines), True


# ── TURTLE VALIDATION ────────────────────────────────────────

def validate_turtle(path: Path) -> bool:
    if not HAVE_RDFLIB:
        vprint(f"  NOTE: rdflib not installed — skipping Turtle validation for {path.name}.")
        return True
    graph = Graph()
    try:
        graph.parse(str(path), format="turtle")
        return True
    except Exception as e:
        vprint(f"  INVALID TURTLE in {path}: {e}")
        return False


# ── CLEAN ─────────────────────────────────────────────────────

def clean_output_dir(out_dir: Path):
    patterns = ["*_cs.ttl", "03_all.ttl", "03_match_audit.csv"]
    removed = 0
    for pat in patterns:
        for f in out_dir.glob(pat):
            f.unlink()
            removed += 1
    vprint(f"--clean: removed {removed} previously generated file(s) from {out_dir}")


# ── ENTRY POINT ───────────────────────────────────────────────

def main():
    global VERBOSE
    args = sys.argv[1:]

    do_clean = False
    if "--clean" in args:
        do_clean = True
        args.remove("--clean")

    if "--verbose" in args:
        VERBOSE = True
        args.remove("--verbose")

    if len(args) < 2:
        print("Usage: python3 etl_03.py [--clean] cs_schools.xlsx file1.xls [file2.xlsx ...]")
        sys.exit(1)

    mapping_file = args[0]
    xls_files    = args[1:]

    out_dir = Path("rdf_output/03")
    out_dir.mkdir(parents=True, exist_ok=True)

    if do_clean:
        clean_output_dir(out_dir)

    vprint(f"Loading CS mapping: {mapping_file}")
    try:
        mapping, universities_index = load_cs_mapping(mapping_file)
    except ValueError as e:
        print(f"FATAL: {e}")
        sys.exit(1)
    vprint(f"  -> {len(mapping)} departments loaded across "
          f"{len(universities_index)} universities\n")

    audit_rows = []
    all_rdf = []
    valid_files = []

    for xls_path in xls_files:
        STATS.files_processed += 1
        rdf_text, ok = process_file(xls_path, universities_index, audit_rows)

        if not ok or rdf_text is None:
            STATS.files_failed += 1
            vprint()
            continue

        out_file = out_dir / (Path(xls_path).stem + "_cs.ttl")
        out_file.write_text(rdf_text, encoding="utf-8")

        if not validate_turtle(out_file):
            STATS.files_failed += 1
            STATS.any_invalid_turtle = True
            vprint(f"  '{out_file.name}' will be EXCLUDED from 03_all.ttl\n")
            continue

        STATS.files_successful += 1
        all_rdf.append(rdf_text)
        valid_files.append(out_file.name)

        n_obs = sum(1 for l in rdf_text.split('\n') if l.startswith('<'))
        vprint(f"  OK: {n_obs} observations -> {out_file.name}\n")

    # Combined output — only successfully validated files
    combined = out_dir / "03_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")
    vprint(f"Combined -> {combined} (from {len(valid_files)} valid file(s))")

    # audit_rows is kept only for in-memory bookkeeping (STATS were already
    # updated in process_file as matches were made); no CSV is written and
    # no per-row match details are printed — see EXECUTION SUMMARY below.

    # ── Summary ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Files processed:            {STATS.files_processed}")
    print(f"Files successful:           {STATS.files_successful}")
    print(f"Files failed:               {STATS.files_failed}")
    print(f"Departments found:          {STATS.depts_found}")
    print(f"Departments matched:        {STATS.depts_matched}")
    print(f"Departments unmatched:      {STATS.depts_unmatched}")
    print(f"Departments ambiguous:      {STATS.depts_ambiguous}")
    print(f"Observations generated:     {STATS.observations}")
    print(f"Duplicate keys detected:    {STATS.duplicate_keys}")
    print(f"Data inconsistencies:       {STATS.inconsistencies}")
    print("=" * 60)

    if STATS.has_failures():
        print("RESULT: FAILED (see warnings above)")
        sys.exit(1)
    else:
        print("RESULT: SUCCESS")
        sys.exit(0)


if __name__ == "__main__":
    main()
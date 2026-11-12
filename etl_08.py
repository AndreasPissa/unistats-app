"""
ELSTAT Table 08 -> RDF Turtle
==============================

Dynamic, mapping-driven ETL for degree-grade classifications by gender.

Design:
- cs_schools.xlsx is the ONLY department whitelist.
- No hardcoded university/department aliases.
- Header rows and grade-category columns are detected dynamically.
- Expected source grade groups are read from the Excel header:
    ΣΥΝΟΛΟ, ΚΑΛΩΣ, ΛΙΑΝ ΚΑΛΩΣ, ΑΡΙΣΤΑ
  each with ΣΥΝΟΛΟ / ΑΡΡΕΝΕΣ / ΘΗΛΕΙΣ columns.
- Program URIs use the exact department URL from cs_schools.xlsx plus "#BSc".
- Supports .xls and .xlsx.
- Zero-count observations are omitted by default.
- Matching diagnostics are written to an audit CSV.
- Cross-checks category totals against the source overall total when possible.

Usage:
    python3 etl_08.py --clean cs_schools.xlsx data/08/*.xls data/08/*.xlsx

Options:
    --clean         remove previous Table 08 generated outputs
    --verbose       print matching/data diagnostics
    --quiet         print only errors and final summary
    --include-zero  include explicit zero-count observations
    --version       print script version

Dependencies:
    pip install pandas xlrd openpyxl
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import unicodedata
import warnings
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl.packaging.custom")

SCRIPT_VERSION = "08-dynamic-mapping-v1-2026-08-26"
TABLE_CODE = "08"
PROGRAM_TYPE = "BSc"

BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"

DATASET_URI = DATASET + "graduatesByDegreeGradeDataset"

MAPPING_COLUMNS = [
    "id", "univ_gr", "school_gr", "dept_gr",
    "univ_uri", "school_uri", "dept_uri", "duration",
]

# Generic Greek/Latin look-alike repair found in ELSTAT files.
# This is NOT a university/department alias list.
CONFUSABLES = str.maketrans({
    "A": "Α", "B": "Β", "E": "Ε", "H": "Η", "I": "Ι", "K": "Κ",
    "M": "Μ", "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ",
    "Y": "Υ", "Z": "Ζ",
})

STOPWORDS = {
    "ΚΑΙ", "ΤΗΣ", "ΤΩΝ", "ΤΟΥ", "ΤΟ", "Η", "Ο", "ΟΙ", "ΤΑ",
    "ΤΗΝ", "ΤΗ", "ΣΕ", "ΜΕ", "ΓΙΑ",
}

REQUIRED_GRADE_RESOURCES = {"Good", "VeryGood", "Excellent"}


@dataclass
class MappingEntry:
    id: str
    univ_gr: str
    school_gr: str
    dept_gr: str
    univ_uri: str
    school_uri: str
    dept_uri: str
    duration: Optional[int]
    univ_norm: str
    school_norm: str
    dept_full_norm: str
    dept_core_norm: str
    location_norm: str


@dataclass
class MatchResult:
    status: str
    entry: Optional[MappingEntry] = None
    university_score: float = 0.0
    school_score: float = 0.0
    department_score: float = 0.0
    location_score: float = 0.0
    coverage: float = 0.0
    combined_score: float = 0.0
    margin: float = 0.0


@dataclass
class GradeGroup:
    resource: str
    label: str
    total_col: int
    male_col: int
    female_col: int


# ---------------------------------------------------------------------------
# Normalization / matching
# ---------------------------------------------------------------------------

def normalize(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value).upper().strip().translate(CONFUSABLES)
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    text = text.replace("&", " ΚΑΙ ").replace("–", "-").replace("—", "-")

    # Generic ELSTAT abbreviations only; not department-specific aliases.
    replacements = [
        (r"\bΗ\s*/\s*Υ\b", " ΥΠΟΛΟΓΙΣΤΩΝ "),
        (r"\bΥΠ\s*/\s*ΣΤΩΝ\b", " ΥΠΟΛΟΓΙΣΤΩΝ "),
        (r"\bΜΗΧ\s*/\s*ΚΩΝ\b", " ΜΗΧΑΝΙΚΩΝ "),
        (r"\bΗΛ\s*/\s*ΓΩΝ\b", " ΗΛΕΚΤΡΟΛΟΓΩΝ "),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)

    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def department_full(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"^\s*ΤΜΗΜΑ\s+", "", text, flags=re.IGNORECASE)
    return normalize(text)


def department_core(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"^\s*ΤΜΗΜΑ\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    return normalize(text)


def trailing_location(value: object) -> str:
    match = re.search(r"\(([^()]*)\)\s*$", str(value))
    return normalize(match.group(1)) if match else ""


def tokens(value: object) -> list[str]:
    return [
        token for token in normalize(value).split()
        if token not in STOPWORDS and len(token) > 1
    ]


def _common_prefix_length(a: str, b: str) -> int:
    length = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        length += 1
    return length


def token_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0

    short, long = (a, b) if len(a) <= len(b) else (b, a)
    ratio = len(short) / len(long)

    # Generic prefix abbreviation handling.
    if len(short) >= 2 and ratio <= 0.65 and long.startswith(short):
        return 0.68 + 0.22 * ratio

    seq = SequenceMatcher(None, a, b).ratio()
    prefix = _common_prefix_length(a, b)

    # Permit inflectional endings, but not arbitrary common suffixes.
    if seq >= 0.84 and prefix >= min(4, math.ceil(0.65 * min(len(a), len(b)))):
        return seq

    return 0.0


def soft_token_similarity(a: object, b: object) -> float:
    left = tokens(a)
    right = tokens(b)
    if not left or not right:
        return 0.0

    p_left = sum(max(token_similarity(x, y) for y in right) for x in left) / len(left)
    p_right = sum(max(token_similarity(y, x) for x in left) for y in right) / len(right)

    if p_left + p_right == 0:
        return 0.0
    return 2 * p_left * p_right / (p_left + p_right)


def _trigrams(value: object) -> set[str]:
    text = " " + normalize(value) + " "
    return {text[i:i + 3] for i in range(max(0, len(text) - 2))}


def field_similarity(a: object, b: object) -> float:
    left = normalize(a)
    right = normalize(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    ga = _trigrams(left)
    gb = _trigrams(right)
    trigram = len(ga & gb) / len(ga | gb) if (ga | gb) else 0.0
    sequence = SequenceMatcher(None, left, right).ratio()

    return (
        0.65 * soft_token_similarity(left, right)
        + 0.20 * trigram
        + 0.15 * sequence
    )


def target_token_coverage(source_dept: object, target_dept_core: object) -> float:
    source_tokens = tokens(source_dept)
    target_tokens = list(dict.fromkeys(tokens(target_dept_core)))
    if not source_tokens or not target_tokens:
        return 0.0

    matched = 0
    for target in target_tokens:
        best = max(token_similarity(source, target) for source in source_tokens)
        if best >= 0.68:
            matched += 1
    return matched / len(target_tokens)


# ---------------------------------------------------------------------------
# cs_schools.xlsx mapping
# ---------------------------------------------------------------------------

def load_mapping(mapping_path: str) -> list[MappingEntry]:
    raw = pd.read_excel(mapping_path, header=0, dtype=object)

    if raw.shape[1] < 8:
        raise ValueError(
            f"Invalid mapping file: expected at least 8 mapping columns, found {raw.shape[1]}. "
            "Make sure cs_schools.xlsx is the first argument."
        )

    # Extra columns hold optional location metadata; identity uses A:H.
    # Ignore completely empty mapping rows while preserving Excel row numbers.
    df = raw.iloc[:, :8].dropna(how="all").copy()
    df.columns = MAPPING_COLUMNS

    entries: list[MappingEntry] = []
    seen_uris: set[str] = set()

    for index, row in df.iterrows():
        univ = "" if pd.isna(row["univ_gr"]) else str(row["univ_gr"]).strip()
        school = "" if pd.isna(row["school_gr"]) else str(row["school_gr"]).strip()
        dept = "" if pd.isna(row["dept_gr"]) else str(row["dept_gr"]).strip()
        dept_uri = "" if pd.isna(row["dept_uri"]) else str(row["dept_uri"]).strip()
        univ_uri = "" if pd.isna(row["univ_uri"]) else str(row["univ_uri"]).strip()
        school_uri = "" if pd.isna(row["school_uri"]) else str(row["school_uri"]).strip()

        if not univ or not dept or not dept_uri:
            raise ValueError(
                f"Mapping row {index + 2} is missing university, department or URI"
            )

        if dept_uri in seen_uris:
            raise ValueError(f"Duplicate department URI in mapping: {dept_uri}")
        seen_uris.add(dept_uri)

        duration = None
        if not pd.isna(row["duration"]):
            try:
                duration = int(row["duration"])
            except (TypeError, ValueError):
                raise ValueError(f"Invalid duration at mapping row {index + 2}")

        entries.append(MappingEntry(
            id=str(row["id"]),
            univ_gr=univ,
            school_gr=school,
            dept_gr=dept,
            univ_uri=univ_uri,
            school_uri=school_uri,
            dept_uri=dept_uri,
            duration=duration,
            univ_norm=normalize(univ),
            school_norm=normalize(school),
            dept_full_norm=department_full(dept),
            dept_core_norm=department_core(dept),
            location_norm=trailing_location(dept),
        ))

    if not entries:
        raise ValueError("Mapping file contains no usable rows")

    return entries


def match_department(
    source_university: str,
    source_school: str,
    source_department: str,
    mapping: list[MappingEntry],
) -> MatchResult:
    # 1) Exact full department match. This can survive institutional changes.
    source_full = department_full(source_department)
    exact = [entry for entry in mapping if entry.dept_full_norm == source_full]

    if len(exact) == 1:
        entry = exact[0]
        return MatchResult(
            status="MATCHED_EXACT",
            entry=entry,
            university_score=field_similarity(source_university, entry.univ_norm),
            school_score=field_similarity(source_school, entry.school_norm),
            department_score=1.0,
            location_score=1.0 if entry.location_norm else 0.0,
            coverage=1.0,
            combined_score=1.0,
            margin=1.0,
        )

    # If the same exact department name occurs more than once in the mapping,
    # use university/school context and refuse near-ties.
    if len(exact) > 1:
        contextual = []
        for entry in exact:
            u = field_similarity(source_university, entry.univ_norm)
            s = field_similarity(source_school, entry.school_norm)
            score = 0.70 * u + 0.30 * s
            contextual.append((score, entry, u, s))

        contextual.sort(key=lambda x: x[0], reverse=True)

        if len(contextual) == 1 or contextual[0][0] - contextual[1][0] >= 0.08:
            score, entry, u, s = contextual[0]
            return MatchResult(
                status="MATCHED_EXACT",
                entry=entry,
                university_score=u,
                school_score=s,
                department_score=1.0,
                coverage=1.0,
                combined_score=score,
                margin=score - contextual[1][0] if len(contextual) > 1 else 1.0,
            )

        return MatchResult(status="AMBIGUOUS")

    # 2) Mapping-driven fuzzy match.
    university_scores: dict[str, float] = {}
    for entry in mapping:
        university_scores[entry.univ_norm] = max(
            university_scores.get(entry.univ_norm, 0.0),
            field_similarity(source_university, entry.univ_norm),
        )

    best_university_score = max(university_scores.values(), default=0.0)
    if best_university_score >= 0.95:
        university_delta = 0.12
    elif best_university_score >= 0.75:
        university_delta = 0.30
    else:
        university_delta = 0.22

    plausible_universities = {
        university for university, score in university_scores.items()
        if score >= 0.20 and score >= best_university_score - university_delta
    }

    scored = []
    source_location = trailing_location(source_department)

    for entry in mapping:
        if entry.univ_norm not in plausible_universities:
            continue

        u = university_scores[entry.univ_norm]
        s = field_similarity(source_school, entry.school_norm) if source_school else 0.0
        d = field_similarity(department_core(source_department), entry.dept_core_norm)

        if source_location and entry.location_norm:
            loc = field_similarity(source_location, entry.location_norm)
        elif entry.location_norm:
            loc = max(
                field_similarity(entry.location_norm, source_university),
                field_similarity(entry.location_norm, source_school),
                field_similarity(entry.location_norm, source_department),
            )
        else:
            loc = 0.0

        coverage = target_token_coverage(
            department_core(source_department),
            entry.dept_core_norm,
        )

        combined = 0.55 * d + 0.20 * u + 0.10 * s + 0.15 * loc
        scored.append((combined, entry, u, s, d, loc, coverage))

    if not scored:
        return MatchResult(status="UNMATCHED")

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1.0
    margin = best[0] - second_score if len(scored) > 1 else 1.0

    combined, entry, u, s, d, loc, coverage = best
    exact_core = department_core(source_department) == entry.dept_core_norm

    # Critical semantic guard against unrelated departments inside the same
    # institution being mapped to a CS department.
    semantic_ok = exact_core or coverage >= 0.60

    if not semantic_ok or d < 0.40 or combined < 0.52:
        return MatchResult(
            status="UNMATCHED",
            entry=entry,
            university_score=u,
            school_score=s,
            department_score=d,
            location_score=loc,
            coverage=coverage,
            combined_score=combined,
            margin=margin,
        )

    if margin < 0.02:
        return MatchResult(
            status="AMBIGUOUS",
            entry=entry,
            university_score=u,
            school_score=s,
            department_score=d,
            location_score=loc,
            coverage=coverage,
            combined_score=combined,
            margin=margin,
        )

    return MatchResult(
        status="MATCHED_FUZZY",
        entry=entry,
        university_score=u,
        school_score=s,
        department_score=d,
        location_score=loc,
        coverage=coverage,
        combined_score=combined,
        margin=margin,
    )


# ---------------------------------------------------------------------------
# Excel / Table 08 header detection
# ---------------------------------------------------------------------------

def read_excel_file(path: str) -> pd.DataFrame:
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return pd.read_excel(path, engine="xlrd", header=None, dtype=object)
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl", header=None, dtype=object)
    raise ValueError(f"Unsupported file type: {suffix}")


def detect_academic_year(df: pd.DataFrame, filename: str) -> str:
    for row_index in range(min(20, len(df))):
        for value in df.iloc[row_index].tolist():
            if pd.isna(value):
                continue

            match = re.search(r"(20\d{2})\s*[/\-]\s*(20\d{2})", str(value))
            if match:
                first, second = int(match.group(1)), int(match.group(2))
                if second == first + 1:
                    return f"AY{first}_{second}"

    match = re.search(r"_(20\d{2})_(20\d{2})(?:_|\.)", filename)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
        if second == first + 1:
            return f"AY{first}_{second}"

    match = re.search(r"_(20\d{2})_", filename)
    if match:
        first = int(match.group(1))
        return f"AY{first}_{first + 1}"

    raise ValueError(f"Could not detect academic year for {filename}")


def is_total_header(value: object) -> bool:
    text = normalize(value)
    return "ΣΥΝΟΛ" in text


def is_male_header(value: object) -> bool:
    text = normalize(value)
    return "ΑΡΡΕΝ" in text


def is_female_header(value: object) -> bool:
    text = normalize(value)
    return "ΘΗΛ" in text


def count_gender_triplets(df: pd.DataFrame, row_index: int) -> int:
    count = 0
    for col in range(max(0, df.shape[1] - 2)):
        if (
            is_total_header(df.iat[row_index, col])
            and is_male_header(df.iat[row_index, col + 1])
            and is_female_header(df.iat[row_index, col + 2])
        ):
            count += 1
    return count


def detect_gender_row(df: pd.DataFrame) -> int:
    """
    Find the header row containing the repeated:
        ΣΥΝΟΛΟ | ΑΡΡΕΝΕΣ | ΘΗΛΕΙΣ
    triplets.

    This avoids hardcoding different row numbers for different ELSTAT years.
    """
    candidates = []

    for row_index in range(min(30, len(df))):
        triplets = count_gender_triplets(df, row_index)
        if triplets:
            candidates.append((triplets, row_index))

    if not candidates:
        raise ValueError(
            "Could not detect the Table 08 gender header row "
            "(ΣΥΝΟΛΟ / ΑΡΡΕΝΕΣ / ΘΗΛΕΙΣ)."
        )

    # Prefer the row with the greatest number of validated triplets.
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    triplets, row_index = candidates[0]

    if triplets < 4:
        raise ValueError(
            f"Detected only {triplets} Total/Male/Female group(s); "
            "expected the overall group plus ΚΑΛΩΣ, ΛΙΑΝ ΚΑΛΩΣ and ΑΡΙΣΤΑ."
        )

    return row_index


def canonical_grade_header(value: object) -> Optional[tuple[str, str]]:
    """
    Convert the source header to a stable RDF resource/label.

    Returns:
        ("Overall",   "ΣΥΝΟΛΟ")
        ("Good",      "ΚΑΛΩΣ")
        ("VeryGood",  "ΛΙΑΝ ΚΑΛΩΣ")
        ("Excellent", "ΑΡΙΣΤΑ")
    """
    text = normalize(value)
    if not text:
        return None

    if "ΛΙΑΝ" in text and "ΚΑΛΩΣ" in text:
        return "VeryGood", "ΛΙΑΝ ΚΑΛΩΣ"

    if "ΑΡΙΣΤ" in text:
        return "Excellent", "ΑΡΙΣΤΑ"

    # Must be checked after ΛΙΑΝ ΚΑΛΩΣ because ΚΑΛΩΣ is a substring of it.
    if "ΚΑΛΩΣ" in text:
        return "Good", "ΚΑΛΩΣ"

    if "ΣΥΝΟΛ" in text:
        return "Overall", "ΣΥΝΟΛΟ"

    return None


def detect_grade_groups(df: pd.DataFrame) -> tuple[list[GradeGroup], int]:
    """
    Detect Table 08 category triples from the actual Excel header.

    We first detect every ΣΥΝΟΛΟ/ΑΡΡΕΝΕΣ/ΘΗΛΕΙΣ triple, then search upward
    from the total column to identify which grade category it belongs to.
    """
    gender_row = detect_gender_row(df)
    groups: list[GradeGroup] = []
    unlabeled_triplets: list[int] = []

    for col in range(max(0, df.shape[1] - 2)):
        if not (
            is_total_header(df.iat[gender_row, col])
            and is_male_header(df.iat[gender_row, col + 1])
            and is_female_header(df.iat[gender_row, col + 2])
        ):
            continue

        category = None

        # Search the nearby header rows above the gender row.
        for row_index in range(gender_row - 1, max(-1, gender_row - 10), -1):
            category = canonical_grade_header(df.iat[row_index, col])
            if category is not None:
                break

        if category is None:
            unlabeled_triplets.append(col)
            continue

        resource, label = category
        groups.append(
            GradeGroup(
                resource=resource,
                label=label,
                total_col=col,
                male_col=col + 1,
                female_col=col + 2,
            )
        )

    if unlabeled_triplets:
        excel_columns = ", ".join(str(col + 1) for col in unlabeled_triplets)
        raise ValueError(
            "Found Total/Male/Female column groups without a recognized Table 08 "
            f"category at Excel column number(s): {excel_columns}. "
            "Refusing to silently drop columns."
        )

    by_resource: dict[str, GradeGroup] = {}
    for group in groups:
        if group.resource in by_resource:
            raise ValueError(f"Duplicate Table 08 category detected: {group.label}")
        by_resource[group.resource] = group

    missing = (REQUIRED_GRADE_RESOURCES | {"Overall"}) - set(by_resource)
    if missing:
        raise ValueError(
            "Missing expected Table 08 category group(s): "
            + ", ".join(sorted(missing))
        )

    ordered = [
        by_resource["Overall"],
        by_resource["Good"],
        by_resource["VeryGood"],
        by_resource["Excellent"],
    ]
    return ordered, gender_row


def detect_data_start(df: pd.DataFrame, gender_row: int) -> int:
    """
    Find the ΓΕΝΙΚΟ ΣΥΝΟΛΟ row dynamically rather than using a fixed row index.
    """
    for row_index in range(gender_row + 1, len(df)):
        value = df.iat[row_index, 0] if df.shape[1] else None
        if pd.isna(value):
            continue
        if "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in normalize(value):
            return row_index

    raise ValueError("Could not locate the 'ΓΕΝΙΚΟ ΣΥΝΟΛΟ' data-start row")


# ---------------------------------------------------------------------------
# Values / RDF
# ---------------------------------------------------------------------------

def to_int(value: object) -> Optional[int]:
    if value is None or pd.isna(value):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip().replace(",", ".")
        if not text or text in {"-", ".", ".."}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None

    if math.isnan(number) or math.isinf(number):
        return None

    if not number.is_integer():
        raise ValueError(f"Expected integer count, got {value!r}")

    return int(number)


def make_program_uri(dept_uri: str) -> str:
    # Preserve the cs_schools.xlsx department URL exactly and add a fragment.
    # Example:
    # https://www.csd.uoc.gr/ -> https://www.csd.uoc.gr/#BSc
    return f"{dept_uri}#{PROGRAM_TYPE}"


# ---------------------------------------------------------------------------
# File ETL
# ---------------------------------------------------------------------------

def process_file(
    path: str,
    mapping: list[MappingEntry],
    allowed_uris: set[str],
    obs_counter: int,
    include_zero: bool,
    verbose: bool,
) -> tuple[str, list[dict], dict, int]:
    filename = Path(path).name

    df = read_excel_file(path)
    academic_year = detect_academic_year(df, filename)
    groups, gender_row = detect_grade_groups(df)
    data_start = detect_data_start(df, gender_row)

    group_by_resource = {group.resource: group for group in groups}
    overall = group_by_resource["Overall"]
    grade_groups = [
        group_by_resource["Good"],
        group_by_resource["VeryGood"],
        group_by_resource["Excellent"],
    ]

    current_university = ""
    current_school = ""

    audit: list[dict] = []
    matched_uris: set[str] = set()

    ambiguous = 0
    inconsistencies = 0
    source_department_rows = 0

    # Key: (mapping department URI, grade resource)
    # Values aggregate ELSTAT rows/tracks mapped to the same department.
    aggregate: dict[tuple[str, str], dict] = {}

    for row_index in range(data_start, len(df)):
        row = df.iloc[row_index]

        c0 = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
        c1 = "" if pd.isna(row.iloc[1]) else str(row.iloc[1]).strip()
        c2 = "" if pd.isna(row.iloc[2]) else str(row.iloc[2]).strip()

        if c0:
            # No university-name hardcoding. Region headings may temporarily
            # become the current context; the following university row replaces
            # them before its department rows are reached.
            current_university = c0
            current_school = ""
            continue

        if c1:
            current_school = c1
            continue

        if not c2:
            continue

        source_department_rows += 1
        match = match_department(
            current_university,
            current_school,
            c2,
            mapping,
        )
        entry = match.entry

        audit.append({
            "source_file": filename,
            "academic_year": academic_year,
            "source_row": row_index + 1,
            "source_university": current_university,
            "source_school": current_school,
            "source_department": c2,
            "mapping_id": entry.id if entry else "",
            "mapping_university": entry.univ_gr if entry else "",
            "mapping_school": entry.school_gr if entry else "",
            "mapping_department": entry.dept_gr if entry else "",
            "mapping_uri": entry.dept_uri if entry else "",
            "university_score": round(match.university_score, 4),
            "school_score": round(match.school_score, 4),
            "department_score": round(match.department_score, 4),
            "location_score": round(match.location_score, 4),
            "coverage": round(match.coverage, 4),
            "combined_score": round(match.combined_score, 4),
            "margin": round(match.margin, 4),
            "status": match.status,
        })

        if match.status == "AMBIGUOUS":
            ambiguous += 1
            if verbose:
                print(f"    AMBIGUOUS: {current_university} | {c2}")
            continue

        if not match.status.startswith("MATCHED") or entry is None:
            if verbose:
                print(f"    UNMATCHED: {current_university} | {c2}")
            continue

        dept_uri = entry.dept_uri
        if dept_uri not in allowed_uris:
            raise RuntimeError(f"Whitelist violation: {dept_uri}")
        matched_uris.add(dept_uri)

        # Read overall source totals for validation.
        overall_total = to_int(row.iloc[overall.total_col])
        overall_male = to_int(row.iloc[overall.male_col])
        overall_female = to_int(row.iloc[overall.female_col])

        if any(
            value is not None and value < 0
            for value in (overall_total, overall_male, overall_female)
        ):
            inconsistencies += 1
            if verbose:
                print(
                    f"    NEGATIVE OVERALL row {row_index + 1}: "
                    f"{current_university} | {c2}"
                )
            continue

        if (
            overall_total is not None
            and overall_male is not None
            and overall_female is not None
            and overall_male + overall_female != overall_total
        ):
            inconsistencies += 1
            if verbose:
                print(
                    f"    INCONSISTENT OVERALL row {row_index + 1}: {c2} | "
                    f"total={overall_total}, male={overall_male}, female={overall_female}"
                )

        source_grade_values: dict[str, tuple[Optional[int], Optional[int], Optional[int]]] = {}

        for group in grade_groups:
            total = to_int(row.iloc[group.total_col])
            male = to_int(row.iloc[group.male_col])
            female = to_int(row.iloc[group.female_col])

            source_grade_values[group.resource] = (total, male, female)

            if total is None and male is None and female is None:
                continue

            if any(value is not None and value < 0 for value in (total, male, female)):
                inconsistencies += 1
                if verbose:
                    print(
                        f"    NEGATIVE row {row_index + 1}: {c2} | {group.label}"
                    )
                continue

            if (
                total is not None
                and male is not None
                and female is not None
                and male + female != total
            ):
                inconsistencies += 1
                if verbose:
                    print(
                        f"    INCONSISTENT row {row_index + 1}: {c2} | "
                        f"{group.label}: total={total}, male={male}, female={female}"
                    )
                # Do not generate observations from an internally inconsistent
                # category triplet.
                continue

            key = (dept_uri, group.resource)
            bucket = aggregate.setdefault(key, {
                "label": group.label,
                "total": 0,
                "male": 0,
                "female": 0,
                "has_total": False,
                "has_male": False,
                "has_female": False,
                "source_departments": set(),
            })

            if total is not None:
                bucket["total"] += total
                bucket["has_total"] = True
            if male is not None:
                bucket["male"] += male
                bucket["has_male"] = True
            if female is not None:
                bucket["female"] += female
                bucket["has_female"] = True

            bucket["source_departments"].add(c2)

        # Cross-check grade categories against the overall source totals when
        # all required component values are available.
        totals = [source_grade_values[r][0] for r in ("Good", "VeryGood", "Excellent")]
        males = [source_grade_values[r][1] for r in ("Good", "VeryGood", "Excellent")]
        females = [source_grade_values[r][2] for r in ("Good", "VeryGood", "Excellent")]

        if overall_total is not None and all(value is not None for value in totals):
            grade_sum = sum(value for value in totals if value is not None)
            if grade_sum != overall_total:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    GRADE SUM mismatch row {row_index + 1}: {c2} | "
                        f"grades={grade_sum}, overall={overall_total}"
                    )

        if overall_male is not None and all(value is not None for value in males):
            male_sum = sum(value for value in males if value is not None)
            if male_sum != overall_male:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    MALE GRADE SUM mismatch row {row_index + 1}: {c2} | "
                        f"grades={male_sum}, overall={overall_male}"
                    )

        if overall_female is not None and all(value is not None for value in females):
            female_sum = sum(value for value in females if value is not None)
            if female_sum != overall_female:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    FEMALE GRADE SUM mismatch row {row_index + 1}: {c2} | "
                        f"grades={female_sum}, overall={overall_female}"
                    )

    lines: list[str] = []
    observations = 0

    for (dept_uri, grade_resource), bucket in sorted(aggregate.items()):
        total = bucket["total"] if bucket["has_total"] else None
        program_uri = make_program_uri(dept_uri)
        ay_uri = f"{RESOURCE}{academic_year}"
        grade_uri = f"{RESOURCE}{grade_resource}"
        source_label = " + ".join(sorted(bucket["source_departments"]))

        for gender, field in (("Male", "male"), ("Female", "female")):
            if not bucket[f"has_{field}"]:
                continue

            count = bucket[field]

            if count == 0 and not include_zero:
                continue

            if total is not None and total > 0:
                percentage = round(count / total * 100, 1)
            elif total == 0 and count == 0:
                percentage = 0.0
            else:
                percentage = None

            obs_counter += 1
            observations += 1
            obs_uri = f"{RESOURCE}08_{academic_year}/obs_grade_{obs_counter}"

            lines.append("")
            lines.append(
                f"# {source_label[:120]} | {academic_year} | "
                f"{bucket['label']} | {gender}"
            )
            lines.append(f"<{obs_uri}>")
            lines.append(f"    a <{QB}Observation> ;")
            lines.append(f"    <{QB}dataSet> <{DATASET_URI}> ;")
            lines.append(f"    <{DIMENSION}academicYear> <{ay_uri}> ;")
            lines.append(f"    <{DIMENSION}program> <{program_uri}> ;")
            lines.append(f"    <{DIMENSION}degreeGrade> <{grade_uri}> ;")
            lines.append(f"    <{DIMENSION}gender> <{RESOURCE}{gender}> ;")
            lines.append(f'    <{DIMENSION}count> "{count}"^^<{XSD}integer> ;')

            if percentage is None:
                lines.append(
                    f'    <{DIMENSION}percentage> ""^^<{XSD}string> .'
                )
            else:
                lines.append(
                    f'    <{DIMENSION}percentage> "{percentage}"^^<{XSD}decimal> .'
                )

    # Absolute whitelist invariant.
    outside = matched_uris - allowed_uris
    if outside:
        raise RuntimeError(
            "Generated data contains URI(s) outside cs_schools.xlsx: "
            + ", ".join(sorted(outside))
        )

    header = (
        "# ============================================================\n"
        "# 08 — BSc Graduates by Degree Grade and Gender\n"
        f"# Source: {filename}\n"
        f"# Academic Year: {academic_year}\n"
        "# Grade groups detected: ΣΥΝΟΛΟ, ΚΑΛΩΣ, ΛΙΑΝ ΚΑΛΩΣ, ΑΡΙΣΤΑ\n"
        f"# Mapping departments found: {len(matched_uris)} / {len(mapping)}\n"
        f"# Observations generated: {observations}\n"
        "# ============================================================\n"
    )

    stats = {
        "academic_year": academic_year,
        "matched_departments": len(matched_uris),
        "source_department_rows": source_department_rows,
        "observations": observations,
        "ambiguous": ambiguous,
        "inconsistencies": inconsistencies,
    }

    return header + "\n".join(lines) + "\n", audit, stats, obs_counter


# ---------------------------------------------------------------------------
# CLI / output
# ---------------------------------------------------------------------------

def clean_output(out_dir: Path) -> None:
    if not out_dir.exists():
        return

    for pattern in ("*_cs.ttl", "08_all.ttl", "08_match_audit.csv"):
        for path in out_dir.glob(pattern):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ELSTAT Table 08 -> RDF Turtle with dynamic grade columns and "
            "cs_schools.xlsx as the exact department whitelist"
        )
    )
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--include-zero", action="store_true")
    parser.add_argument("--version", action="version", version=SCRIPT_VERSION)
    parser.add_argument("mapping_file")
    parser.add_argument("excel_files", nargs="+")
    args = parser.parse_args()

    try:
        mapping = load_mapping(args.mapping_file)
    except Exception as exc:
        print(f"FATAL: {exc}")
        return 1

    allowed_uris = {entry.dept_uri for entry in mapping}
    if len(allowed_uris) != len(mapping):
        print("FATAL: mapping contains duplicate department URIs")
        return 1

    out_dir = Path("rdf_output/08")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        clean_output(out_dir)

    if not args.quiet:
        print(f"ETL 08 [{SCRIPT_VERSION}]")
        print(f"cs_schools whitelist: {len(mapping)} departments")

    all_rdf: list[str] = []
    all_audit: list[dict] = []

    obs_counter = 0
    files_ok = 0
    files_failed = 0
    total_observations = 0
    total_ambiguous = 0
    total_inconsistencies = 0

    for number, path in enumerate(args.excel_files, start=1):
        filename = Path(path).name

        try:
            rdf, audit, stats, obs_counter = process_file(
                path=path,
                mapping=mapping,
                allowed_uris=allowed_uris,
                obs_counter=obs_counter,
                include_zero=args.include_zero,
                verbose=args.verbose,
            )
        except Exception as exc:
            files_failed += 1
            print(f"ERROR [{filename}]: {exc}")
            continue

        out_file = out_dir / f"{Path(path).stem}_cs.ttl"
        out_file.write_text(rdf, encoding="utf-8")

        all_rdf.append(rdf)
        all_audit.extend(audit)

        files_ok += 1
        total_observations += stats["observations"]
        total_ambiguous += stats["ambiguous"]
        total_inconsistencies += stats["inconsistencies"]

        if not args.quiet:
            print(
                f"[{number}/{len(args.excel_files)}] {filename}: "
                f"{stats['matched_departments']}/{len(mapping)} mapping depts | "
                f"{stats['observations']} obs | "
                f"{stats['inconsistencies']} consistency warning(s)"
            )

    combined = out_dir / "08_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")

    audit_file = out_dir / "08_match_audit.csv"
    audit_fields = [
        "source_file", "academic_year", "source_row",
        "source_university", "source_school", "source_department",
        "mapping_id", "mapping_university", "mapping_school",
        "mapping_department", "mapping_uri",
        "university_score", "school_score", "department_score",
        "location_score", "coverage", "combined_score", "margin", "status",
    ]

    with audit_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        writer.writerows(all_audit)

    print(
        f"Done: {files_ok}/{len(args.excel_files)} files | "
        f"{total_observations} observations | "
        "outside-whitelist URIs: 0"
    )

    if args.verbose:
        print(f"Ambiguous source department rows: {total_ambiguous}")
        print(f"Data consistency warnings: {total_inconsistencies}")
        print(f"Combined RDF: {combined}")
        print(f"Audit: {audit_file}")

    # Ambiguous matches are intentionally treated as a failed run because they
    # require inspection instead of silent guessing.
    return 1 if files_failed or total_ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())

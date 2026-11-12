"""
ETL Script: ELSTAT Table 06 -> RDF Turtle
Students by birth year, study year and gender.

The cs_schools.xlsx workbook is the ONLY department whitelist.
Every RDF program URI is taken directly from that workbook.

Usage:
    python3 etl_06.py --clean cs_schools.xlsx data/06/*.xls data/06/*.xlsx

Options:
    --clean         remove previous Table 06 generated outputs
    --verbose       print matching diagnostics
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

SCRIPT_VERSION = "06-dynamic-columns-v7-2026-08-26"
PROGRAM_TYPE = "BSc"

BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"
DATASET_URI = DATASET + "studentsByBirthYearStudyYearDataset"

MAPPING_COLUMNS = [
    "id", "univ_gr", "school_gr", "dept_gr",
    "univ_uri", "school_uri", "dept_uri", "duration",
]

# Generic Greek/Latin look-alike normalization. This is not a university or
# department alias list; it only repairs mixed Latin characters found in ELSTAT.
CONFUSABLES = str.maketrans({
    "A": "Α", "B": "Β", "E": "Ε", "H": "Η", "I": "Ι", "K": "Κ",
    "M": "Μ", "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ",
    "Y": "Υ", "Z": "Ζ",
})

STOPWORDS = {
    "ΚΑΙ", "ΤΗΣ", "ΤΩΝ", "ΤΟΥ", "ΤΟ", "Η", "Ο", "ΟΙ", "ΤΑ",
    "ΤΗΝ", "ΤΗ", "ΣΕ", "ΜΕ", "ΓΙΑ",
}


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
class BirthGroup:
    label: str
    total_col: int
    male_col: int
    female_col: int


# ---------------------------------------------------------------------------
# Normalization / similarity
# ---------------------------------------------------------------------------

def normalize(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""

    text = str(value).upper().strip().translate(CONFUSABLES)
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    text = text.replace("&", " ΚΑΙ ").replace("–", "-").replace("—", "-")

    # Generic, format-level abbreviation used throughout ELSTAT files.
    # This is intentionally not tied to any specific department/university.
    text = re.sub(r"\bΗ\s*/\s*Υ\b", " ΥΠΟΛΟΓΙΣΤΩΝ ", text)

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

    # Generic abbreviation handling: e.g. ΥΠ -> ΥΠΟΛΟΓΙΣΤΩΝ,
    # ΠΛΗΡΟΦΟΡ -> ΠΛΗΡΟΦΟΡΙΑΚΩΝ. Only prefix abbreviations count.
    if len(short) >= 2 and ratio <= 0.65 and long.startswith(short):
        return 0.68 + 0.22 * ratio

    seq = SequenceMatcher(None, a, b).ratio()
    prefix = _common_prefix_length(a, b)

    # Handle inflectional endings, but do not accept words that merely share
    # a suffix (important for e.g. ΓΕΩΠΛΗΡΟΦΟΡΙΚΗΣ vs ΠΛΗΡΟΦΟΡΙΚΗΣ).
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
# Mapping
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
            raise ValueError(f"Mapping row {index + 2} is missing university, department or URI")
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


def exact_department_candidates(source_dept: str, mapping: list[MappingEntry]) -> list[MappingEntry]:
    """
    Exact full department name (including location) is the strongest possible
    evidence and can survive an institutional reorganisation. Example: a
    department can move university while retaining the exact same name/location.
    """
    source_full = department_full(source_dept)
    if not source_full:
        return []
    return [entry for entry in mapping if entry.dept_full_norm == source_full]


def match_department(
    source_university: str,
    source_school: str,
    source_department: str,
    mapping: list[MappingEntry],
) -> MatchResult:
    # 1) Exact full department match from cs_schools.xlsx.
    exact = exact_department_candidates(source_department, mapping)
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

    # If an exact name is duplicated in the mapping, use context; do not guess.
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

    # 2) Mapping-driven fuzzy match. First estimate which mapping universities
    # are plausible. This prevents a good department-name resemblance from
    # jumping to an unrelated institution.
    university_scores: dict[str, float] = {}
    for entry in mapping:
        university_scores[entry.univ_norm] = max(
            university_scores.get(entry.univ_norm, 0.0),
            field_similarity(source_university, entry.univ_norm),
        )

    if not university_scores:
        return MatchResult(status="UNMATCHED")

    best_university_score = max(university_scores.values())
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

        university_score = university_scores[entry.univ_norm]
        school_score = field_similarity(source_school, entry.school_norm) if source_school else 0.0
        department_score = field_similarity(department_core(source_department), entry.dept_core_norm)

        if source_location and entry.location_norm:
            location_score = field_similarity(source_location, entry.location_norm)
        elif entry.location_norm:
            location_score = max(
                field_similarity(entry.location_norm, source_university),
                field_similarity(entry.location_norm, source_school),
                field_similarity(entry.location_norm, source_department),
            )
        else:
            location_score = 0.0

        coverage = target_token_coverage(
            department_core(source_department),
            entry.dept_core_norm,
        )

        combined = (
            0.55 * department_score
            + 0.20 * university_score
            + 0.10 * school_score
            + 0.15 * location_score
        )

        scored.append((combined, entry, university_score, school_score,
                       department_score, location_score, coverage))

    if not scored:
        return MatchResult(status="UNMATCHED")

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1.0
    margin = best[0] - second_score if len(scored) > 1 else 1.0

    combined, entry, university_score, school_score, department_score, location_score, coverage = best
    exact_core = department_core(source_department) == entry.dept_core_norm

    # Semantic guard: matching cannot be based only on university/school/location.
    # The source must cover most of the target department's meaningful tokens,
    # or have the same normalized department core. This rejects e.g. Topography
    # / Geoinformatics being mistaken for Informatics simply because the campus
    # and university are the same.
    semantic_ok = exact_core or coverage >= 0.60

    if not semantic_ok or department_score < 0.40 or combined < 0.52:
        return MatchResult(
            status="UNMATCHED",
            entry=entry,
            university_score=university_score,
            school_score=school_score,
            department_score=department_score,
            location_score=location_score,
            coverage=coverage,
            combined_score=combined,
            margin=margin,
        )

    if margin < 0.02:
        return MatchResult(
            status="AMBIGUOUS",
            entry=entry,
            university_score=university_score,
            school_score=school_score,
            department_score=department_score,
            location_score=location_score,
            coverage=coverage,
            combined_score=combined,
            margin=margin,
        )

    return MatchResult(
        status="MATCHED_FUZZY",
        entry=entry,
        university_score=university_score,
        school_score=school_score,
        department_score=department_score,
        location_score=location_score,
        coverage=coverage,
        combined_score=combined,
        margin=margin,
    )


# ---------------------------------------------------------------------------
# Excel / Table 06 detection
# ---------------------------------------------------------------------------

def read_excel_file(path: str) -> pd.DataFrame:
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return pd.read_excel(path, engine="xlrd", header=None, dtype=object)
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl", header=None, dtype=object)
    raise ValueError(f"Unsupported file type: {suffix}")


def detect_academic_year(df: pd.DataFrame, filename: str) -> str:
    for row_index in range(min(15, len(df))):
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


def canonical_birth_label(value: object) -> Optional[str]:
    if value is None or pd.isna(value):
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if number.is_integer() and 1900 <= int(number) <= 2100:
                return str(int(number))
        except Exception:
            pass

    # IMPORTANT: do not use normalize() first for ranges, because normalize()
    # removes punctuation. We must preserve the hyphen in labels such as
    # "1969-1973".
    raw = str(value).upper().strip()
    raw = "".join(
        ch for ch in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(ch)
    )
    raw = raw.replace("–", "-").replace("—", "-")
    raw = re.sub(r"\s+", " ", raw).strip()

    if re.fullmatch(r"\d{4}(?:\.0)?", raw):
        return raw.split(".")[0]

    if re.fullmatch(r"\d{4}\s*-\s*\d{4}", raw):
        return re.sub(r"\s+", "", raw)

    normalized = normalize(raw)
    if "ΑΓΝΩΣΤ" in normalized:
        return "AGNOSTO"
    if "ΠΡΙΝ" in normalized:
        year = re.search(r"\d{4}", raw)
        return f"{year.group(0)} KAI PRIN" if year else "KAI PRIN"

    return None

def detect_header_rows(df: pd.DataFrame) -> tuple[int, int]:
    label_row = None
    gender_row = None

    for row_index in range(min(20, len(df))):
        values = [normalize(value) for value in df.iloc[row_index].tolist() if pd.notna(value)]
        joined = " | ".join(values)

        if "ΕΚΠΑΙΔΕΥΤΙΚΟ ΙΔΡΥΜΑ" in joined and "ΤΜΗΜΑ" in joined:
            label_row = row_index
        if "ΣΥΝΟΛΟ" in joined and "ΑΡΡΕΝ" in joined and "ΘΗΛ" in joined:
            gender_row = row_index

    if label_row is None or gender_row is None:
        raise ValueError("Could not detect Table 06 header rows")

    return label_row, gender_row


def detect_birth_groups(df: pd.DataFrame) -> list[BirthGroup]:
    """
    Detect every birth-year Total/Male/Female triple dynamically.

    We do NOT assume that all birth labels are on one fixed row. For every
    gender triple starting from column G, the function searches upward in the
    header area for the corresponding year/range/unknown label.

    If a valid Total/Male/Female triple has no birth label, the file fails
    instead of silently dropping columns.
    """
    label_row, gender_row = detect_header_rows(df)
    groups: list[BirthGroup] = []
    unlabeled_triplets: list[int] = []

    def gender_header(column: int) -> str:
        if column >= df.shape[1] or pd.isna(df.iat[gender_row, column]):
            return ""
        return normalize(df.iat[gender_row, column])

    # D/E/F are the overall Total/Male/Female. Birth groups begin at G.
    for column in range(6, df.shape[1] - 2):
        if not (
            "ΣΥΝΟΛΟ" in gender_header(column)
            and "ΑΡΡΕΝ" in gender_header(column + 1)
            and "ΘΗΛ" in gender_header(column + 2)
        ):
            continue

        label = None

        # Search upward because some ELSTAT workbooks use extra/merged
        # header rows. This also protects future years from layout changes.
        for row_index in range(gender_row - 1, max(-1, gender_row - 10), -1):
            candidate = canonical_birth_label(df.iat[row_index, column])
            if candidate is not None:
                label = candidate
                break

        # Fallback to the row identified as the structural label row.
        if label is None:
            candidate = canonical_birth_label(df.iat[label_row, column])
            if candidate is not None:
                label = candidate

        if label is None:
            unlabeled_triplets.append(column)
            continue

        groups.append(BirthGroup(label, column, column + 1, column + 2))

    if unlabeled_triplets:
        cols = ", ".join(str(col + 1) for col in unlabeled_triplets)
        raise ValueError(
            "Found Total/Male/Female birth-group columns without a birth-year "
            f"label at Excel column number(s): {cols}. "
            "Refusing to silently omit them."
        )

    if not groups:
        raise ValueError("No birth-year groups detected")

    labels = [group.label for group in groups]
    if len(set(labels)) != len(labels):
        raise ValueError(f"Duplicate birth-year labels detected: {labels}")

    return groups

def parse_study_year(value: object) -> Optional[tuple[str, str]]:
    text = normalize(value)
    if not text:
        return None

    match = re.fullmatch(r"([1-9])\s*[ΟO]?\s*ΕΤΟΣ", text)
    if match:
        number = int(match.group(1))
        return f"StudyYear{number}", f"{number}ο ΕΤΟΣ"

    if "ΠΕΡΑΝ" in text and "ΚΑΝΟΝΙΚ" in text and "ΕΤ" in text:
        return "BeyondNormalYears", "ΠΕΡΑΝ ΤΩΝ ΚΑΝΟΝΙΚΩΝ ΕΤΩΝ"

    return None


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


def ttl_literal(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def ttl_birth_year(label: str) -> str:
    if re.fullmatch(r"\d{4}", label):
        return f'"{label}"^^<{XSD}gYear>'
    return f'{ttl_literal(label)}^^<{XSD}string>'


# ---------------------------------------------------------------------------
# File ETL
# ---------------------------------------------------------------------------

def process_file(
    path: str,
    mapping: list[MappingEntry],
    allowed_uris: set[str],
    global_observation_counter: int,
    include_zero: bool,
    verbose: bool,
) -> tuple[str, list[dict], dict, int]:
    filename = Path(path).name
    df = read_excel_file(path)
    academic_year = detect_academic_year(df, filename)
    birth_groups = detect_birth_groups(df)

    audit: list[dict] = []
    current_university = ""
    current_school = ""
    active_match: Optional[MatchResult] = None
    active_source_department = ""

    # Aggregate several ELSTAT rows/tracks that resolve to the same cs_schools
    # entry. Key excludes gender; values hold total/male/female sums.
    aggregate: dict[tuple[str, str, str], dict] = {}
    matched_uris: set[str] = set()
    inconsistencies = 0
    ambiguous = 0
    source_department_rows = 0

    for row_index in range(len(df)):
        row = df.iloc[row_index]
        c0 = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
        c1 = "" if pd.isna(row.iloc[1]) else str(row.iloc[1]).strip()
        c2 = "" if pd.isna(row.iloc[2]) else str(row.iloc[2]).strip()

        # Table 06 is hierarchical by columns A/B/C. We intentionally do not
        # hardcode university names. Any non-study label in A establishes a new
        # top-level institution/section; a real university row will replace a
        # preceding region heading before department rows are reached.
        if c0:
            if parse_study_year(c0) is None and "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" not in normalize(c0):
                current_university = c0
                current_school = ""
                active_match = None
                active_source_department = ""
            continue

        if c1:
            if parse_study_year(c1) is None:
                current_school = c1
                active_match = None
                active_source_department = ""
            continue

        if not c2:
            continue

        study = parse_study_year(c2)

        # Department/track header.
        if study is None:
            source_department_rows += 1
            active_source_department = c2
            active_match = match_department(
                current_university,
                current_school,
                c2,
                mapping,
            )

            entry = active_match.entry
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
                "university_score": round(active_match.university_score, 4),
                "school_score": round(active_match.school_score, 4),
                "department_score": round(active_match.department_score, 4),
                "location_score": round(active_match.location_score, 4),
                "coverage": round(active_match.coverage, 4),
                "combined_score": round(active_match.combined_score, 4),
                "margin": round(active_match.margin, 4),
                "status": active_match.status,
            })

            if active_match.status == "AMBIGUOUS":
                ambiguous += 1
            elif active_match.status.startswith("MATCHED") and entry:
                uri = entry.dept_uri
                if uri not in allowed_uris:
                    raise RuntimeError(f"Internal whitelist violation: {uri}")
                matched_uris.add(uri)

            if verbose and active_match.status in {"AMBIGUOUS", "UNMATCHED"}:
                print(f"    {active_match.status}: {current_university} | {c2}")
            continue

        # Study-year data only belongs to a validated mapping entry.
        if (
            active_match is None
            or not active_match.status.startswith("MATCHED")
            or active_match.entry is None
        ):
            continue

        study_resource, study_label = study
        entry = active_match.entry
        dept_uri = entry.dept_uri

        for group in birth_groups:
            total = to_int(row.iloc[group.total_col])
            male = to_int(row.iloc[group.male_col])
            female = to_int(row.iloc[group.female_col])

            if total is None and male is None and female is None:
                continue

            if any(value is not None and value < 0 for value in (total, male, female)):
                inconsistencies += 1
                continue

            if total is not None and male is not None and female is not None and male + female != total:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    INCONSISTENT row {row_index + 1}: {c2} | "
                        f"{study_label} | birth {group.label}: "
                        f"total={total}, male={male}, female={female}"
                    )
                continue

            key = (dept_uri, study_resource, group.label)
            bucket = aggregate.setdefault(key, {
                "total": 0,
                "male": 0,
                "female": 0,
                "has_total": False,
                "has_male": False,
                "has_female": False,
                "source_departments": set(),
                "study_label": study_label,
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
            bucket["source_departments"].add(active_source_department)

    lines: list[str] = []
    observations = 0

    # Stable ordering makes diffs reproducible.
    for (dept_uri, study_resource, birth_label), bucket in sorted(aggregate.items()):
        total = bucket["total"] if bucket["has_total"] else None

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

            global_observation_counter += 1
            observations += 1
            obs_uri = f"{RESOURCE}06_{academic_year}/obs_{global_observation_counter}"
            ay_uri = f"{RESOURCE}{academic_year}"
            program_uri = f"{dept_uri}#{PROGRAM_TYPE}"
            study_uri = f"{RESOURCE}{study_resource}"

            source_label = " + ".join(sorted(bucket["source_departments"]))
            lines.append("")
            lines.append(
                f"# {source_label[:120]} | {bucket['study_label']} | "
                f"birth {birth_label} | {gender}"
            )
            lines.append(f"<{obs_uri}>")
            lines.append(f"    a <{QB}Observation> ;")
            lines.append(f"    <{QB}dataSet> <{DATASET_URI}> ;")
            lines.append(f"    <{DIMENSION}academicYear> <{ay_uri}> ;")
            lines.append(f"    <{DIMENSION}program> <{program_uri}> ;")
            lines.append(f"    <{DIMENSION}studyYear> <{study_uri}> ;")
            lines.append(f"    <{DIMENSION}gender> <{RESOURCE}{gender}> ;")
            lines.append(f"    <{DIMENSION}birthYear> {ttl_birth_year(birth_label)} ;")
            lines.append(f'    <{DIMENSION}count> "{count}"^^<{XSD}integer> ;')
            if percentage is None:
                lines.append(f'    <{DIMENSION}percentage> ""^^<{XSD}string> .')
            else:
                lines.append(f'    <{DIMENSION}percentage> "{percentage}"^^<{XSD}decimal> .')

    # Absolute whitelist invariant.
    outside = matched_uris - allowed_uris
    if outside:
        raise RuntimeError(
            "Generated data contains URI(s) outside cs_schools.xlsx: "
            + ", ".join(sorted(outside))
        )

    header = (
        "# ============================================================\n"
        "# 06 — BSc Students by Birth Year and Study Year\n"
        f"# Source: {filename}\n"
        f"# Academic Year: {academic_year}\n"
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
        "missing_mapping_departments": len(mapping) - len(matched_uris),
    }

    return header + "\n".join(lines) + "\n", audit, stats, global_observation_counter


# ---------------------------------------------------------------------------
# CLI / output
# ---------------------------------------------------------------------------

def clean_output(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    for pattern in ("*_cs.ttl", "06_all.ttl", "06_match_audit.csv"):
        for path in out_dir.glob(pattern):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ELSTAT Table 06 -> RDF Turtle using cs_schools.xlsx as exact whitelist"
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
        print("FATAL: mapping contains duplicate normalized department URIs")
        return 1

    out_dir = Path("rdf_output/06")
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        clean_output(out_dir)

    if not args.quiet:
        print(f"ETL 06 [{SCRIPT_VERSION}]")
        print(f"cs_schools whitelist: {len(mapping)} departments")

    all_rdf: list[str] = []
    all_audit: list[dict] = []
    observation_counter = 0
    files_ok = 0
    files_failed = 0
    total_observations = 0
    total_ambiguous = 0
    total_inconsistencies = 0

    for number, path in enumerate(args.excel_files, start=1):
        filename = Path(path).name
        try:
            rdf, audit, stats, observation_counter = process_file(
                path,
                mapping,
                allowed_uris,
                observation_counter,
                include_zero=args.include_zero,
                verbose=args.verbose,
            )
        except Exception as exc:
            files_failed += 1
            print(f"ERROR [{filename}]: {exc}")
            continue

        output_file = out_dir / f"{Path(path).stem}_cs.ttl"
        output_file.write_text(rdf, encoding="utf-8")

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
                f"{stats['observations']} obs"
            )

    combined = out_dir / "06_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")

    audit_file = out_dir / "06_match_audit.csv"
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
        f"outside-whitelist URIs: 0"
    )

    if args.verbose:
        print(f"Ambiguous source department rows: {total_ambiguous}")
        print(f"Data inconsistencies: {total_inconsistencies}")
        print(f"Combined RDF: {combined}")
        print(f"Audit: {audit_file}")

    return 1 if files_failed or total_ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())
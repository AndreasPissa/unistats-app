"""
ELSTAT Table 22 -> RDF Turtle
==============================

Teaching staff that teaches in the department but belongs to another department,
by staff category and gender.

Each inspected workbook contains two sheets: 22Y and 22ΑY.
This ETL processes only 22Y (category + gender). 22ΑY is the separate
birth-year table and is intentionally not mixed into this dataset.

Design:
- cs_schools.xlsx is the ONLY department whitelist.
- Department identity/URI always comes from cs_schools.xlsx.
- Main Table 22Y sheet is selected dynamically.
- Header rows and all Total/Male/Female category triples are detected dynamically.
- Multi-level category headers are reconstructed from the actual Excel.
- No hardcoded data-start row.
- Supports .xls and .xlsx.
- Zero-count gender observations are omitted by default.
- Matching diagnostics are written to 22_match_audit.csv.
- Internal consistency checks verify Male + Female = Total.
- Parent category totals are cross-checked against their components where possible.
- staffCategory URIs use the exact concept names from concepts_21.ttl.
- General/parent/category totals are validation-only and are NOT emitted as observations.
- Unknown non-total categories cause an error instead of creating ad-hoc staffCategory URIs.
- Standalone CollaboratingResearchStaff is preserved; if detailed WithPhD/WithoutPhD branches exist, only the detailed branches are emitted.
- Source hierarchy inconsistencies are written to 22_hierarchy_audit.csv.

Usage:
    python3 etl_22.py --clean cs_schools.xlsx data/22/*.xls data/22/*.xlsx

Options:
    --clean         remove previous Table 22 generated outputs
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

SCRIPT_VERSION = "22-staff-concepts-v3-2026-09-15"
TABLE_CODE = "22"

BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"

DATASET_URI = DATASET + "externalTeachingStaffByCategoryDataset"

MAPPING_COLUMNS = [
    "id", "univ_gr", "school_gr", "dept_gr",
    "univ_uri", "school_uri", "dept_uri", "duration",
]

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
class StaffGroup:
    code: str
    label: str
    main_code: str
    qualifier_code: str
    subtype_code: str
    total_col: int
    male_col: int
    female_col: int


# ---------------------------------------------------------------------------
# Text normalization / department matching
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

    # Generic ELSTAT abbreviations; not institution/department-specific aliases.
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


def structural_text(value: object) -> str:
    """
    Table 22 workbooks can contain numeric placeholders in hierarchy columns in the
    institution/school/department hierarchy columns. They are not labels.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ""

    text = str(value).strip()
    if not text:
        return ""

    # Numeric/string placeholders are not hierarchy labels.
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return ""

    return text


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

    if len(short) >= 2 and ratio <= 0.65 and long.startswith(short):
        return 0.68 + 0.22 * ratio

    seq = SequenceMatcher(None, a, b).ratio()
    prefix = _common_prefix_length(a, b)

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
    # Exact full department name is strongest evidence.
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
# Excel / Table 22Y detection
# ---------------------------------------------------------------------------

def excel_engine(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return "xlrd"
    if suffix == ".xlsx":
        return "openpyxl"
    raise ValueError(f"Unsupported file type: {suffix}")


def read_excel_sheet(path: str, sheet_name: object) -> pd.DataFrame:
    return pd.read_excel(
        path,
        sheet_name=sheet_name,
        engine=excel_engine(path),
        header=None,
        dtype=object,
    )


def select_table22_sheet(path: str) -> tuple[object, pd.DataFrame]:
    """
    Select the main Table 22Y sheet dynamically.

    Newer workbooks may also contain a separate 22AY birth-year sheet.
    We deliberately select the category/gender table, not the birth-year table.
    """
    engine = excel_engine(path)
    book = pd.ExcelFile(path, engine=engine)

    candidates = []

    for sheet_name in book.sheet_names:
        preview = pd.read_excel(
            book,
            sheet_name=sheet_name,
            header=None,
            nrows=20,
            dtype=object,
        )

        joined = " ".join(
            normalize(value)
            for value in preview.to_numpy().ravel()
            if not pd.isna(value)
        )
        sheet_norm = normalize(sheet_name)

        score = 0
        if "22Y" in str(sheet_name).upper():
            score += 120
        if "ΔΙΔΑΚΤΙΚΟ ΠΡΟΣΩΠΙΚΟ" in joined:
            score += 30
        if "ΑΝΗΚΕΙ ΣΕ ΑΛΛΟ ΤΜΗΜΑ" in joined:
            score += 50
        if "ΚΑΤΗΓΟΡΙΑ" in joined:
            score += 50
        if "ΚΑΘΗΓΗΤ" in joined or "ΛΕΚΤΟΡ" in joined:
            score += 20
        if "ΕΤΟΣ ΓΕΝΝΗΣΗΣ" in joined:
            score -= 100
        if "22ΑY" in sheet_norm or "22AY" in str(sheet_name).upper():
            score -= 120

        candidates.append((score, sheet_name))

    if not candidates:
        raise ValueError("Workbook contains no sheets")

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_sheet = candidates[0]

    if best_score <= 0:
        raise ValueError(
            "Could not identify the main Table 22Y sheet "
            "(external teaching staff by category and gender)."
        )

    return best_sheet, read_excel_sheet(path, best_sheet)


def detect_academic_year(df: pd.DataFrame, filename: str) -> str:
    for row_index in range(min(25, len(df))):
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
    return "ΣΥΝΟΛ" in text or text == "Σ"


def is_male_header(value: object) -> bool:
    text = normalize(value)
    return "ΑΡΡΕΝ" in text or text == "Α"


def is_female_header(value: object) -> bool:
    text = normalize(value)
    return "ΘΗΛ" in text or text == "Θ"


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
    candidates = []

    for row_index in range(min(35, len(df))):
        triplets = count_gender_triplets(df, row_index)
        if triplets:
            candidates.append((triplets, row_index))

    if not candidates:
        raise ValueError(
            "Could not detect Table 22 Total/Male/Female header row."
        )

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    triplets, row_index = candidates[0]

    if triplets < 2:
        raise ValueError(
            f"Detected only {triplets} Total/Male/Female group(s); "
            "this does not look like Table 22Y."
        )

    return row_index


def detect_main_header_row(df: pd.DataFrame, gender_row: int) -> int:
    """
    Find the highest-level category header row. It should contain the overall
    total and several teaching-staff family labels.
    """
    best = None

    for row_index in range(gender_row):
        nonempty = []
        for col in range(3, df.shape[1]):
            value = df.iat[row_index, col]
            if pd.isna(value):
                continue
            text = str(value).strip()
            if text:
                nonempty.append((col, text))

        if not nonempty:
            continue

        joined = " | ".join(normalize(text) for _, text in nonempty)
        score = len(nonempty)

        if "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in joined:
            score += 20
        if "ΔΙΔΑΚΤ" in joined or "ΕΡΕΥΝ" in joined:
            score += 10
        if any(col == 3 for col, _ in nonempty):
            score += 5

        if best is None or score > best[0]:
            best = (score, row_index)

    if best is None:
        raise ValueError("Could not detect Table 22 category-header row")

    return best[1]


def canonical_main_code(label: str) -> str:
    text = normalize(label)

    if "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in text:
        return "AllTeachingStaff"
    if "ΤΑΚΤΙΚ" in text and ("ΔΕΠ" in text or "ΕΡΕΥΝΗΤΙΚ" in text):
        return "RegularTeachingResearchStaff"
    if "ΕΚΤΑΚΤ" in text:
        return "TemporaryTeachingStaff"
    if "ΣΥΝΕΡΓΑΖ" in text and "ΕΡΕΥΝ" in text:
        return "CollaboratingResearchStaff"
    if "ΕΡΓΑΣΤΗΡΙΑΚ" in text and ("ΕΔΙΠ" in text or "ΕΙΔΙΚ" in text):
        return "SpecialLaboratoryTeachingStaff"
    if "ΜΕΤΑΠΤΥΧΙΑΚ" in text and "ΥΠΟΤΡΟΦ" in text:
        return "PostgraduateScholars"
    if "ΒΟΗΘΗΤΙΚ" in text and "ΔΙΔΑΚΤΙΚ" in text:
        return "AuxiliaryTeachingStaff"
    if "ΛΟΙΠ" in text:
        return "OtherTeachingStaff"

    # Stable fallback for future/unknown categories.
    compact = re.sub(r"[^A-Z0-9Α-Ω]+", "_", text).strip("_")
    return "SourceCategory_" + compact


def canonical_qualifier_code(label: str) -> str:
    text = normalize(label)
    if "ΧΩΡΙΣ" in text and "ΔΙΔΑΚΤΟΡΙΚ" in text:
        return "WithoutPhD"
    if "ΜΕ" in text and "ΔΙΔΑΚΤΟΡΙΚ" in text:
        return "WithPhD"

    compact = re.sub(r"[^A-Z0-9Α-Ω]+", "_", text).strip("_")
    return "Qualifier_" + compact if compact else ""


def canonical_subtype_code(label: str) -> str:
    text = normalize(label)

    if "ΣΥΝΟΛ" in text:
        return "Total"
    if "ΑΝΑΠΛΗΡΩΤ" in text:
        return "AssociateProfessor"
    if "ΕΠΙΚΟΥΡ" in text:
        return "AssistantProfessor"
    if "ΚΑΘΗΓΗΤ" in text and "ΕΠΙΣΚΕΠΤ" not in text:
        return "Professor"
    if "ΛΕΚΤΟΡ" in text:
        return "Lecturer"
    if "ΕΠΙΣΚΕΠΤ" in text and "ΚΑΘΗΓΗΤ" in text:
        return "VisitingProfessor"
    if "ΜΕΤΑ" in text and "ΔΙΔΑΚΤΟΡΙΚ" in text and "ΕΡΕΥΝ" in text:
        return "PostdoctoralResearcher"
    if "ΕΝΤΕΤΑΛΜ" in text:
        return "AssignedTeachingStaff"
    if "ΠΑΝΕΠΙΣΤΗΜΙΑΚ" in text and "ΥΠΟΤΡΟΦ" in text:
        return "UniversityScholar"
    if "407" in text:
        return "Law407ContractStaff"
    if "ΕΠΙΜΕΛΗΤ" in text:
        return "Curator"
    if text.startswith("ΒΟΗΘ"):
        return "Assistant"
    if "ΕΠΙΣΤΗΜΟΝΙΚ" in text and "ΣΥΝΕΡΓΑΤ" in text:
        return "ScientificAssociate"

    compact = re.sub(r"[^A-Z0-9Α-Ω]+", "_", text).strip("_")
    return "Subtype_" + compact if compact else ""



# ---------------------------------------------------------------------------
# staffCategory vocabulary mapping
# ---------------------------------------------------------------------------

# IMPORTANT:
# These are the LEAF concepts from concepts_21.ttl that may appear in
# qb:Observation -> dimension:staffCategory.
#
# Parent concepts such as AllTeachingStaff, RegularTeachingResearchStaff,
# TemporaryTeachingStaff, CollaboratingResearchStaff and
# AuxiliaryTeachingStaff remain useful in the SKOS concept hierarchy, but
# they are NOT emitted as observations. Source "Σύνολο" columns are also not
# emitted; those totals can be derived by summing the leaf observations.
OBSERVATION_STAFF_CATEGORIES = frozenset({
    "Professor",
    "AssociateProfessor",
    "AssistantProfessor",
    "Lecturer",
    "VisitingProfessor",
    "PostdoctoralResearcher",
    "AssignedTeachingStaff",
    "UniversityScholar",
    "PD407",
    "CollaboratingResearchStaff",
    "CollaboratingResearchStaffWithPhD",
    "CollaboratingResearchStaffWithoutPhD",
    "EDIP",
    "EEP",
    "CuratorWithPhD",
    "CuratorWithoutPhD",
    "AssistantWithPhD",
    "AssistantWithoutPhD",
    "ScientificAssociateWithPhD",
    "ScientificAssociateWithoutPhD",
    "OtherTeachingStaff",
})


def concept_code_for_staff_group(
    group: StaffGroup,
    all_groups: list[StaffGroup],
) -> Optional[str]:
    """
    Map a dynamically reconstructed ELSTAT staff group to the EXACT resource
    name defined in concepts_21.ttl.

    Totals / hierarchy parents are not emitted when their value can be derived
    from more detailed source groups.

    Special case:
    CollaboratingResearchStaff is a legitimate observation concept in
    concepts_21.ttl. If the workbook contains only that standalone category,
    keep it. If the same workbook provides WithPhD/WithoutPhD child groups,
    then the unqualified CollaboratingResearchStaff row is a derivable parent
    total and is skipped.

    Unknown non-total categories are never turned into ad-hoc URIs.
    """
    main = group.main_code
    qualifier = group.qualifier_code
    subtype = group.subtype_code

    # ΓΕΝΙΚΟ ΣΥΝΟΛΟ: always derivable from detailed categories.
    if main == "AllTeachingStaff":
        return None

    # Τακτικό διδακτικό - ερευνητικό προσωπικό (ΔΕΠ)
    if main == "RegularTeachingResearchStaff":
        if subtype in {"", "Total"}:
            return None
        mapping = {
            "Professor": "Professor",
            "AssociateProfessor": "AssociateProfessor",
            "AssistantProfessor": "AssistantProfessor",
            "Lecturer": "Lecturer",
        }
        if subtype in mapping:
            return mapping[subtype]

    # Έκτακτο διδακτικό προσωπικό
    if main == "TemporaryTeachingStaff":
        if subtype in {"", "Total"}:
            return None
        mapping = {
            "VisitingProfessor": "VisitingProfessor",
            "PostdoctoralResearcher": "PostdoctoralResearcher",
            "AssignedTeachingStaff": "AssignedTeachingStaff",
            "UniversityScholar": "UniversityScholar",
            "Law407ContractStaff": "PD407",
        }
        if subtype in mapping:
            return mapping[subtype]

    # Συνεργαζόμενο Ερευνητικό Προσωπικό (ΣΕΠ)
    if main == "CollaboratingResearchStaff":
        # Detailed branches, when present, are observations.
        if qualifier == "WithPhD" and subtype in {"", "Total"}:
            return "CollaboratingResearchStaffWithPhD"
        if qualifier == "WithoutPhD" and subtype in {"", "Total"}:
            return "CollaboratingResearchStaffWithoutPhD"

        # The unqualified category is a real observation only if the workbook
        # does NOT provide the two more detailed qualifier branches.
        if not qualifier and subtype in {"", "Total"}:
            has_detailed_children = any(
                other is not group
                and other.main_code == "CollaboratingResearchStaff"
                and other.qualifier_code in {"WithPhD", "WithoutPhD"}
                for other in all_groups
            )
            return None if has_detailed_children else "CollaboratingResearchStaff"

    # Standalone leaf categories.
    if main == "SpecialLaboratoryTeachingStaff":
        if not qualifier and subtype in {"", "Total"}:
            return "EDIP"

    if main == "PostgraduateScholars":
        if not qualifier and subtype in {"", "Total"}:
            return "EEP"

    # Βοηθητικό διδακτικό προσωπικό:
    # overall and qualifier totals are derivable, roles are leaves.
    if main == "AuxiliaryTeachingStaff":
        if subtype in {"", "Total"}:
            return None

        mapping = {
            ("WithPhD", "Curator"): "CuratorWithPhD",
            ("WithoutPhD", "Curator"): "CuratorWithoutPhD",
            ("WithPhD", "Assistant"): "AssistantWithPhD",
            ("WithoutPhD", "Assistant"): "AssistantWithoutPhD",
            ("WithPhD", "ScientificAssociate"): "ScientificAssociateWithPhD",
            ("WithoutPhD", "ScientificAssociate"): "ScientificAssociateWithoutPhD",
        }
        key = (qualifier, subtype)
        if key in mapping:
            return mapping[key]

    if main == "OtherTeachingStaff":
        if not qualifier and subtype in {"", "Total"}:
            return "OtherTeachingStaff"

    raise ValueError(
        "Detected a non-total staff category that has no mapping in "
        "concepts_21.ttl: "
        f"label={group.label!r}, main={main!r}, "
        f"qualifier={qualifier!r}, subtype={subtype!r}"
    )


def _segment_starts(df: pd.DataFrame, row_index: int) -> list[tuple[int, str]]:
    starts = []
    for col in range(3, df.shape[1]):
        value = df.iat[row_index, col]
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            starts.append((col, text))
    return starts


def _label_for_column_within_segment(
    df: pd.DataFrame,
    row_index: int,
    col: int,
    segment_start: int,
    segment_end: int,
) -> str:
    """
    Reconstruct a merged-cell header by taking the last non-empty label in
    this row between the segment start and the target triplet column.
    """
    current = ""

    for c in range(segment_start, min(col, segment_end - 1) + 1):
        value = df.iat[row_index, c]
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            current = text

    return current


def detect_staff_groups(df: pd.DataFrame) -> tuple[list[StaffGroup], int]:
    gender_row = detect_gender_row(df)
    main_row = detect_main_header_row(df, gender_row)

    triplet_cols = []
    for col in range(3, df.shape[1] - 2):
        if (
            is_total_header(df.iat[gender_row, col])
            and is_male_header(df.iat[gender_row, col + 1])
            and is_female_header(df.iat[gender_row, col + 2])
        ):
            triplet_cols.append(col)

    if not triplet_cols:
        raise ValueError("No Table 22 category triplets detected")

    main_starts = _segment_starts(df, main_row)
    if not main_starts:
        raise ValueError("No top-level Table 22 categories detected")

    # Each top-level header spans until the next top-level non-empty header.
    segments = []
    for index, (start_col, label) in enumerate(main_starts):
        end_col = (
            main_starts[index + 1][0]
            if index + 1 < len(main_starts)
            else df.shape[1]
        )
        segments.append((start_col, end_col, label))

    groups: list[StaffGroup] = []

    for col in triplet_cols:
        containing = [
            segment for segment in segments
            if segment[0] <= col < segment[1]
        ]

        if not containing:
            raise ValueError(
                f"Could not associate category triplet at Excel column {col + 1} "
                "with a top-level staff category."
            )

        segment_start, segment_end, main_label = containing[-1]
        main_code = canonical_main_code(main_label)

        # Reconstruct any intermediate/leaf headers inside this top-level segment.
        lower_labels = []
        for row_index in range(main_row + 1, gender_row):
            label = _label_for_column_within_segment(
                df,
                row_index,
                col,
                segment_start,
                segment_end,
            )
            if label and normalize(label) not in {
                normalize(main_label),
                "ΑΡΡΕΝΕΣ",
                "ΘΗΛΕΙΣ",
            }:
                # Deduplicate repeated merged labels.
                if not lower_labels or normalize(lower_labels[-1]) != normalize(label):
                    lower_labels.append(label)

        qualifier_label = ""
        subtype_label = ""

        for label in lower_labels:
            norm = normalize(label)
            # Qualifiers are specifically the auxiliary-staff headings
            # "Με διδακτορικό δίπλωμα" / "Χωρίς διδακτορικό δίπλωμα".
            # Do NOT classify "Μετα/διδακτορικοί ερευνητές" as a qualifier.
            is_phd_qualifier = (
                "ΔΙΔΑΚΤΟΡΙΚ" in norm
                and ("ΧΩΡΙΣ" in norm or "ΔΙΠΛΩΜ" in norm)
                and "ΕΡΕΥΝ" not in norm
            )
            if is_phd_qualifier:
                qualifier_label = label
            else:
                subtype_label = label

        qualifier_code = (
            canonical_qualifier_code(qualifier_label)
            if qualifier_label else ""
        )

        subtype_code = (
            canonical_subtype_code(subtype_label)
            if subtype_label else ""
        )

        # For a top-level category with no lower label, the main category itself
        # is the observed staff category.
        code_parts = [main_code]
        if qualifier_code:
            code_parts.append(qualifier_code)
        if subtype_code:
            code_parts.append(subtype_code)

        code = "_".join(code_parts)

        label_parts = [main_label]
        if qualifier_label:
            label_parts.append(qualifier_label)
        if subtype_label:
            label_parts.append(subtype_label)

        groups.append(StaffGroup(
            code=code,
            label=" / ".join(label_parts),
            main_code=main_code,
            qualifier_code=qualifier_code,
            subtype_code=subtype_code,
            total_col=col,
            male_col=col + 1,
            female_col=col + 2,
        ))

    codes = [group.code for group in groups]
    if len(codes) != len(set(codes)):
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        raise ValueError(
            "Duplicate reconstructed staff-category code(s): "
            + ", ".join(duplicates)
        )

    return groups, gender_row


def detect_data_start(df: pd.DataFrame, gender_row: int) -> int:
    for row_index in range(gender_row + 1, len(df)):
        value = structural_text(df.iat[row_index, 0])
        if value and "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in normalize(value):
            return row_index

    raise ValueError("Could not locate the 'ΓΕΝΙΚΟ ΣΥΝΟΛΟ' data-start row")


# ---------------------------------------------------------------------------
# Values / consistency
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


def validate_row_hierarchy(
    row: pd.Series,
    groups: list[StaffGroup],
    row_number: int,
    department: str,
    verbose: bool,
) -> tuple[int, list[dict]]:
    """
    Validate source consistency without forcing source totals into the RDF.

    Checks:
    1. Each source triplet: Male + Female = Total.
    2. A source parent/Σύνολο equals the sum of its detailed child categories.
    3. Qualifier totals (e.g. WithPhD / WithoutPhD) equal their role children.

    The detailed/leaf values remain authoritative RDF observations. A bad
    source parent total is recorded as an audit warning but never used to
    overwrite the detailed values.
    """
    warnings_count = 0
    issues: list[dict] = []

    values = {}
    for group in groups:
        total = to_int(row.iloc[group.total_col])
        male = to_int(row.iloc[group.male_col])
        female = to_int(row.iloc[group.female_col])
        values[group.code] = (total, male, female)

        if any(v is not None and v < 0 for v in (total, male, female)):
            warnings_count += 1
            issues.append({
                "source_row": row_number,
                "source_department": department,
                "issue_type": "NEGATIVE_VALUE",
                "category": group.label,
                "component": "",
                "source_value": "",
                "derived_value": "",
                "details": f"total={total}, male={male}, female={female}",
            })
            if verbose:
                print(
                    f"    NEGATIVE row {row_number}: {department} | {group.label}"
                )
            continue

        if (
            total is not None
            and male is not None
            and female is not None
            and male + female != total
        ):
            warnings_count += 1
            issues.append({
                "source_row": row_number,
                "source_department": department,
                "issue_type": "GENDER_TOTAL_MISMATCH",
                "category": group.label,
                "component": "total",
                "source_value": total,
                "derived_value": male + female,
                "details": f"male={male}, female={female}",
            })
            if verbose:
                print(
                    f"    INCONSISTENT row {row_number}: {department} | "
                    f"{group.label}: total={total}, male={male}, female={female}"
                )

    # Parent/subcategory checks.
    parents = {}
    for group in groups:
        parent_key = (group.main_code, group.qualifier_code)
        parents.setdefault(parent_key, []).append(group)

    for parent_key, members in parents.items():
        total_member = next(
            (g for g in members if g.subtype_code == "Total"),
            None,
        )
        child_members = [
            g for g in members
            if g.subtype_code and g.subtype_code != "Total"
        ]

        if total_member is None or not child_members:
            continue

        parent_values = values[total_member.code]

        for value_index, value_name in enumerate(("total", "male", "female")):
            parent_value = parent_values[value_index]
            child_values = [values[g.code][value_index] for g in child_members]

            if (
                parent_value is None
                or not child_values
                or any(v is None for v in child_values)
            ):
                continue

            child_sum = sum(v for v in child_values if v is not None)

            if child_sum != parent_value:
                warnings_count += 1
                issues.append({
                    "source_row": row_number,
                    "source_department": department,
                    "issue_type": "PARENT_CHILD_SUM_MISMATCH",
                    "category": total_member.label,
                    "component": value_name,
                    "source_value": parent_value,
                    "derived_value": child_sum,
                    "details": "Derived from: " + " + ".join(
                        g.label for g in child_members
                    ),
                })
                if verbose:
                    print(
                        f"    SUBCATEGORY SUM mismatch row {row_number}: "
                        f"{department} | {total_member.label} | "
                        f"{value_name}: children={child_sum}, parent={parent_value}"
                    )

    return warnings_count, issues


# ---------------------------------------------------------------------------
# ETL
# ---------------------------------------------------------------------------

def process_file(
    path: str,
    mapping: list[MappingEntry],
    allowed_uris: set[str],
    obs_counter: int,
    include_zero: bool,
    verbose: bool,
) -> tuple[str, list[dict], list[dict], dict, int]:
    filename = Path(path).name

    sheet_name, df = select_table22_sheet(path)
    academic_year = detect_academic_year(df, filename)
    groups, gender_row = detect_staff_groups(df)

    # Validate every detected group against concepts_21.ttl before reading data.
    group_concept_codes = [
        concept_code_for_staff_group(group, groups)
        for group in groups
    ]
    emitted_source_groups = sum(
        1 for code in group_concept_codes if code is not None
    )
    observation_concepts = sorted({
        code for code in group_concept_codes if code is not None
    })
    skipped_total_groups = len(groups) - emitted_source_groups

    unknown_concepts = set(observation_concepts) - OBSERVATION_STAFF_CATEGORIES
    if unknown_concepts:
        raise RuntimeError(
            "Internal staffCategory vocabulary violation: "
            + ", ".join(sorted(unknown_concepts))
        )

    data_start = detect_data_start(df, gender_row)

    current_university = ""
    current_school = ""

    audit: list[dict] = []
    hierarchy_audit: list[dict] = []
    matched_uris: set[str] = set()

    ambiguous = 0
    inconsistencies = 0
    source_department_rows = 0

    # Aggregate split/historical source tracks that resolve to one mapping URI.
    # Key: (department URI, staff category code)
    aggregate: dict[tuple[str, str], dict] = {}

    for row_index in range(data_start, len(df)):
        row = df.iloc[row_index]

        c0 = structural_text(row.iloc[0]) if len(row) > 0 else ""
        c1 = structural_text(row.iloc[1]) if len(row) > 1 else ""
        c2 = structural_text(row.iloc[2]) if len(row) > 2 else ""

        if c0:
            if "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in normalize(c0):
                current_university = ""
                current_school = ""
                continue

            # Region rows can appear in the same column. They temporarily
            # replace the context; the actual university row follows before
            # a department row is reached.
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
            "sheet": str(sheet_name),
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

        row_warning_count, row_issues = validate_row_hierarchy(
            row=row,
            groups=groups,
            row_number=row_index + 1,
            department=c2,
            verbose=verbose,
        )
        inconsistencies += row_warning_count

        for issue in row_issues:
            issue.update({
                "source_file": filename,
                "sheet": str(sheet_name),
                "academic_year": academic_year,
                "mapping_department": entry.dept_gr,
                "mapping_uri": dept_uri,
            })
            hierarchy_audit.append(issue)

        for group in groups:
            category_concept = concept_code_for_staff_group(group, groups)

            # Source totals/parent categories are used above for consistency
            # validation only. They are not RDF observations.
            if category_concept is None:
                continue

            total = to_int(row.iloc[group.total_col])
            male = to_int(row.iloc[group.male_col])
            female = to_int(row.iloc[group.female_col])

            if total is None and male is None and female is None:
                continue

            if any(v is not None and v < 0 for v in (total, male, female)):
                continue

            # Do not generate from an internally inconsistent triplet.
            if (
                total is not None
                and male is not None
                and female is not None
                and male + female != total
            ):
                continue

            key = (dept_uri, category_concept)
            bucket = aggregate.setdefault(key, {
                "label": group.label,
                "source_group_code": group.code,
                "concept_code": category_concept,
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

    lines: list[str] = []
    observations = 0

    for (dept_uri, category_code), bucket in sorted(aggregate.items()):
        if category_code not in OBSERVATION_STAFF_CATEGORIES:
            raise RuntimeError(
                f"Refusing to emit unknown staffCategory concept: {category_code}"
            )

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

            obs_counter += 1
            observations += 1

            obs_uri = f"{RESOURCE}22_{academic_year}/obs_staff_{obs_counter}"
            ay_uri = f"{RESOURCE}{academic_year}"
            category_uri = f"{RESOURCE}staffCategory/{category_code}"
            source_label = " + ".join(sorted(bucket["source_departments"]))

            lines.append("")
            lines.append(
                f"# {source_label[:120]} | {academic_year} | "
                f"{bucket['label']} | {gender}"
            )
            lines.append(f"<{obs_uri}>")
            lines.append(f"    a <{QB}Observation> ;")
            lines.append(f"    <{QB}dataSet> <{DATASET_URI}> ;")
            lines.append(f"    <{DIMENSION}academicYear> <{ay_uri}> ;")
            lines.append(f"    <{DIMENSION}department> <{dept_uri}> ;")
            lines.append(f"    <{DIMENSION}staffCategory> <{category_uri}> ;")
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

    outside = matched_uris - allowed_uris
    if outside:
        raise RuntimeError(
            "Generated data contains URI(s) outside cs_schools.xlsx: "
            + ", ".join(sorted(outside))
        )

    header = (
        "# ============================================================\n"
        "# 22 — External Teaching Staff by Category and Gender\n"
        f"# Source: {filename}\n"
        f"# Source sheet: {sheet_name}\n"
        f"# Academic Year: {academic_year}\n"
        f"# Source staff groups detected: {len(groups)}\n"
        f"# Observation staff concepts: {len(observation_concepts)}\n"
        f"# Total/parent groups skipped: {skipped_total_groups}\n"
        f"# Mapping departments found: {len(matched_uris)} / {len(mapping)}\n"
        f"# Observations generated: {observations}\n"
        "# ============================================================\n"
    )

    stats = {
        "academic_year": academic_year,
        "sheet": str(sheet_name),
        "groups": len(groups),
        "observation_concepts": len(observation_concepts),
        "skipped_total_groups": skipped_total_groups,
        "matched_departments": len(matched_uris),
        "source_department_rows": source_department_rows,
        "observations": observations,
        "ambiguous": ambiguous,
        "inconsistencies": inconsistencies,
    }

    return header + "\n".join(lines) + "\n", audit, hierarchy_audit, stats, obs_counter


# ---------------------------------------------------------------------------
# CLI / output
# ---------------------------------------------------------------------------

def clean_output(out_dir: Path) -> None:
    if not out_dir.exists():
        return

    for pattern in ("*_cs.ttl", "22_all.ttl", "22_match_audit.csv", "22_hierarchy_audit.csv"):
        for path in out_dir.glob(pattern):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ELSTAT Table 22Y -> RDF Turtle using cs_schools.xlsx "
            "as the exact department whitelist"
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

    out_dir = Path("rdf_output/22")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        clean_output(out_dir)

    if not args.quiet:
        print(f"ETL 22 [{SCRIPT_VERSION}]")
        print(f"cs_schools whitelist: {len(mapping)} departments")

    all_rdf: list[str] = []
    all_audit: list[dict] = []
    all_hierarchy_audit: list[dict] = []

    obs_counter = 0
    files_ok = 0
    files_failed = 0
    total_observations = 0
    total_ambiguous = 0
    total_inconsistencies = 0

    for number, path in enumerate(args.excel_files, start=1):
        filename = Path(path).name

        try:
            rdf, audit, hierarchy_audit, stats, obs_counter = process_file(
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
        all_hierarchy_audit.extend(hierarchy_audit)

        files_ok += 1
        total_observations += stats["observations"]
        total_ambiguous += stats["ambiguous"]
        total_inconsistencies += stats["inconsistencies"]

        if not args.quiet:
            print(
                f"[{number}/{len(args.excel_files)}] {filename}: "
                f"sheet={stats['sheet']} | "
                f"{stats['groups']} source groups | "
                f"{stats['observation_concepts']} observation concepts | "
                f"{stats['skipped_total_groups']} totals/parents skipped | "
                f"{stats['matched_departments']}/{len(mapping)} mapping depts | "
                f"{stats['observations']} obs | "
                f"{stats['inconsistencies']} consistency warning(s)"
            )

    combined = out_dir / "22_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")

    audit_file = out_dir / "22_match_audit.csv"
    audit_fields = [
        "source_file", "sheet", "academic_year", "source_row",
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

    hierarchy_audit_file = out_dir / "22_hierarchy_audit.csv"
    hierarchy_fields = [
        "source_file", "sheet", "academic_year", "source_row",
        "source_department", "mapping_department", "mapping_uri",
        "issue_type", "category", "component",
        "source_value", "derived_value", "details",
    ]

    with hierarchy_audit_file.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=hierarchy_fields)
        writer.writeheader()
        writer.writerows(all_hierarchy_audit)

    print(
        f"Done: {files_ok}/{len(args.excel_files)} files | "
        f"{total_observations} observations | "
        "outside-whitelist URIs: 0"
    )

    if args.verbose:
        print(f"Ambiguous source department rows: {total_ambiguous}")
        print(f"Data consistency warnings: {total_inconsistencies}")
        print(f"Combined RDF: {combined}")
        print(f"Match audit: {audit_file}")
        print(f"Hierarchy audit: {hierarchy_audit_file}")

    # Ambiguous mappings require inspection rather than silent guessing.
    return 1 if files_failed or total_ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())

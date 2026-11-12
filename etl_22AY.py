"""
ELSTAT Table 22AY -> RDF Turtle
===============================

Teaching staff that teaches in a department but belongs to another department,
by birth-year group and gender.

The source workbooks also contain Table 22Y (staff category + gender).
This script intentionally selects ONLY the 22AY / birth-year sheet.

Design:
- cs_schools.xlsx is the ONLY department whitelist.
- Department identity and URI always come from cs_schools.xlsx.
- The 22AY sheet is selected dynamically from each workbook.
- Birth-year groups are reconstructed from the actual multi-row Excel header.
- No hardcoded birth years, column indexes, header rows, or data-start rows.
- Handles normal 5-year ranges, "XXXX και μετά", "XXXX και πριν", and unknown.
- Safely repairs a clearly identifiable one-character source typo in a 5-year
  range (e.g. 19634-1968 -> 1964-1968) and records the repair in a header audit.
- Supports .xls and .xlsx.
- Zero-count observations are omitted by default.
- Matching diagnostics go to 22AY_match_audit.csv.
- Header-label repairs/detection diagnostics go to 22AY_header_audit.csv.
- Male/Female counts are treated as the primary gender observations.
- Older ELSTAT 22AY files contain unreliable per-birth TOTAL cells even when
  Male/Female columns reconcile perfectly with the overall gender totals.
  Those source-total discrepancies are audited but no longer cause valid
  Male/Female observations to be dropped.
- True gender inconsistencies are still detected and reported.

Usage:
    python3 etl_22AY.py --clean cs_schools.xlsx data/22/*.xls data/22/*.xlsx

Options:
    --clean         remove previous Table 22AY generated outputs
    --verbose       print matching/header/data diagnostics
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

SCRIPT_VERSION = "22AY-dynamic-mapping-v2-2026-09-03"
TABLE_CODE = "22AY"

BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"

DATASET_URI = DATASET + "externalTeachingStaffByBirthYearDataset"

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
class BirthGroup:
    label: str
    total_col: int
    male_col: int
    female_col: int
    raw_header: str
    status: str = "DETECTED"


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
    if not text or re.fullmatch(r"\d+(?:\.0+)?", text):
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
            department_core(source_department), entry.dept_core_norm
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
# Workbook / 22AY sheet selection
# ---------------------------------------------------------------------------

def excel_engine(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return "xlrd"
    if suffix == ".xlsx":
        return "openpyxl"
    raise ValueError(f"Unsupported file type: {suffix}")


def select_22ay_sheet(path: str) -> tuple[object, pd.DataFrame]:
    """Select the birth-year Table 22AY sheet, never the category Table 22Y."""
    engine = excel_engine(path)
    book = pd.ExcelFile(path, engine=engine)
    candidates = []

    for sheet_name in book.sheet_names:
        preview = pd.read_excel(
            book,
            sheet_name=sheet_name,
            header=None,
            nrows=25,
            dtype=object,
        )

        joined = " ".join(
            normalize(value)
            for value in preview.to_numpy().ravel()
            if not pd.isna(value)
        )
        sheet_text = normalize(sheet_name).replace(" ", "")

        score = 0
        if "22" in sheet_text:
            score += 20
        if "ΕΤΟΣ ΓΕΝΝΗΣΗΣ" in joined or ("ΕΤΟΣ" in joined and "ΓΕΝΝ" in joined):
            score += 120
        if "ΔΙΔΑΚΤΙΚΟ ΠΡΟΣΩΠΙΚΟ" in joined:
            score += 25
        if "ΑΝΗΚΕΙ ΣΕ ΑΛΛΟ ΤΜΗΜΑ" in joined or "ΔΕΝ ΑΝΗΚΕΙ" in joined:
            score += 30
        if "ΚΑΤΗΓΟΡΙΑ" in joined and "ΕΤΟΣ ΓΕΝΝΗΣΗΣ" not in joined:
            score -= 80

        candidates.append((score, sheet_name))

    if not candidates:
        raise ValueError("Workbook contains no sheets")

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_sheet = candidates[0]

    if best_score < 100:
        raise ValueError(
            "Could not confidently identify the Table 22AY birth-year sheet."
        )

    df = pd.read_excel(
        path,
        sheet_name=best_sheet,
        engine=engine,
        header=None,
        dtype=object,
    )
    return best_sheet, df


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


# ---------------------------------------------------------------------------
# Dynamic birth-year header detection
# ---------------------------------------------------------------------------

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
            "Could not detect the 22AY Total/Male/Female header row."
        )

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    triplets, row_index = candidates[0]

    if triplets < 4:
        raise ValueError(
            f"Detected only {triplets} Total/Male/Female groups; "
            "this does not look like Table 22AY."
        )

    return row_index


def _strip_accents_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text.upper())
        if not unicodedata.combining(ch)
    )


def _delete_one_matches(raw: str, expected: str) -> bool:
    if len(raw) != len(expected) + 1:
        return False
    return any(raw[:i] + raw[i + 1:] == expected for i in range(len(raw)))


def parse_birth_header_text(raw_header: str) -> tuple[Optional[str], str]:
    """
    Parse a reconstructed header region.

    Returns (canonical_label, status). A clearly repairable one-character typo
    in the start of a 5-year range is repaired and marked REPAIRED_SOURCE_TYPO.
    """
    raw = _strip_accents_upper(raw_header)
    raw = raw.replace("–", "-").replace("—", "-")
    raw = re.sub(r"\s+", " ", raw).strip()

    norm = normalize(raw)
    if "ΑΓΝΩΣΤ" in norm:
        return "AGNOSTO", "DETECTED"

    numbers = re.findall(r"(?<!\d)(\d{4,5})(?!\d)", raw)

    # A normal range, possibly split across multiple header rows/cells.
    if len(numbers) >= 2:
        left_raw, right_raw = numbers[0], numbers[1]

        if len(left_raw) == 4 and len(right_raw) == 4:
            left, right = int(left_raw), int(right_raw)
            if 1900 <= left <= 2100 and 1900 <= right <= 2100:
                lo, hi = sorted((left, right))
                return f"{lo}-{hi}", "DETECTED"

        # Guarded repair for an obvious 5-year-range source typo such as
        # 19634-1968. We derive the expected start as end-4 and require that
        # deleting exactly one character from the malformed token yields it.
        if len(left_raw) == 5 and len(right_raw) == 4:
            right = int(right_raw)
            expected_left = str(right - 4)
            if _delete_one_matches(left_raw, expected_left):
                return f"{expected_left}-{right_raw}", "REPAIRED_SOURCE_TYPO"

        if len(left_raw) == 4 and len(right_raw) == 5:
            left = int(left_raw)
            expected_right = str(left + 4)
            if _delete_one_matches(right_raw, expected_right):
                return f"{left_raw}-{expected_right}", "REPAIRED_SOURCE_TYPO"

        return None, "INVALID_RANGE"

    if len(numbers) == 1:
        year_raw = numbers[0]
        if len(year_raw) != 4:
            return None, "INVALID_YEAR"

        year = int(year_raw)
        if not (1900 <= year <= 2100):
            return None, "INVALID_YEAR"

        if "ΠΡΙΝ" in norm:
            return f"{year} KAI PRIN", "DETECTED"
        if "ΜΕΤΑ" in norm:
            return f"{year} KAI META", "DETECTED"

        # A lone boundary year is resolved later using adjacent 5-year groups.
        return str(year), "BOUNDARY_YEAR_UNRESOLVED"

    return None, "NO_BIRTH_LABEL"


def reconstruct_triplet_header(
    df: pd.DataFrame,
    gender_row: int,
    total_col: int,
) -> str:
    """
    Reconstruct a possibly multi-row / merged birth-year header by collecting
    text from the three columns belonging to the triplet above the gender row.
    """
    pieces = []
    start_row = max(0, gender_row - 12)

    for row_index in range(start_row, gender_row):
        for col in range(total_col, min(total_col + 3, df.shape[1])):
            value = df.iat[row_index, col]
            if pd.isna(value):
                continue
            text = str(value).strip()
            if not text:
                continue
            n = normalize(text)
            # Gender/overall scaffolding is not part of a birth label.
            if n in {"ΣΥΝΟΛΟ", "ΑΡΡΕΝΕΣ", "ΘΗΛΕΙΣ", "Α", "Θ", "Σ"}:
                continue
            if text not in pieces:
                pieces.append(text)

    return " | ".join(pieces)


def _parse_range(label: str) -> Optional[tuple[int, int]]:
    match = re.fullmatch(r"(\d{4})-(\d{4})", label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def resolve_boundary_years(groups: list[BirthGroup]) -> None:
    """
    Resolve first/last lone-year boundary labels from neighboring 5-year ranges.

    Example:
      1999, 1994-1998 -> 1999 KAI META
      1959-1963, 1958 -> 1958 KAI PRIN

    This is data-driven and only applies when the adjacency is exact.
    """
    real_indices = [i for i, g in enumerate(groups) if g.label != "AGNOSTO"]
    if not real_indices:
        return

    first_i = real_indices[0]
    last_i = real_indices[-1]

    first = groups[first_i]
    if re.fullmatch(r"\d{4}", first.label):
        next_i = next((i for i in real_indices if i > first_i), None)
        if next_i is not None:
            next_range = _parse_range(groups[next_i].label)
            if next_range and int(first.label) == next_range[1] + 1:
                first.label = f"{first.label} KAI META"
                first.status = "INFERRED_BOUNDARY_FROM_ADJACENT_RANGE"

    last = groups[last_i]
    if re.fullmatch(r"\d{4}", last.label):
        prev_i = next((i for i in reversed(real_indices) if i < last_i), None)
        if prev_i is not None:
            prev_range = _parse_range(groups[prev_i].label)
            if prev_range and int(last.label) == prev_range[0] - 1:
                last.label = f"{last.label} KAI PRIN"
                last.status = "INFERRED_BOUNDARY_FROM_ADJACENT_RANGE"


def detect_birth_groups(df: pd.DataFrame) -> tuple[list[BirthGroup], int, int]:
    """
    Detect the source overall triplet and all birth-year triplets dynamically.

    Returns (birth_groups, gender_row, overall_total_column).
    """
    gender_row = detect_gender_row(df)

    triplet_cols = []
    for col in range(max(0, df.shape[1] - 2)):
        if (
            is_total_header(df.iat[gender_row, col])
            and is_male_header(df.iat[gender_row, col + 1])
            and is_female_header(df.iat[gender_row, col + 2])
        ):
            triplet_cols.append(col)

    if len(triplet_cols) < 2:
        raise ValueError("Could not detect overall + birth-year triplets")

    # ELSTAT hierarchy columns are A/B/C. The first triplet after them is the
    # source overall Total/Male/Female group.
    overall_col = min(triplet_cols)
    birth_cols = [col for col in triplet_cols if col != overall_col]

    groups = []
    failures = []

    for col in birth_cols:
        raw_header = reconstruct_triplet_header(df, gender_row, col)
        label, status = parse_birth_header_text(raw_header)

        if label is None:
            failures.append((col, raw_header, status))
            continue

        groups.append(BirthGroup(
            label=label,
            total_col=col,
            male_col=col + 1,
            female_col=col + 2,
            raw_header=raw_header,
            status=status,
        ))

    if failures:
        details = "; ".join(
            f"Excel col {col + 1}: {status} [{raw!r}]"
            for col, raw, status in failures
        )
        raise ValueError(
            "Could not safely identify every 22AY birth-year group: " + details
        )

    if not groups:
        raise ValueError("No 22AY birth-year groups detected")

    resolve_boundary_years(groups)

    unresolved = [
        g for g in groups
        if re.fullmatch(r"\d{4}", g.label)
    ]
    if unresolved:
        raise ValueError(
            "Unresolved boundary birth-year label(s): "
            + ", ".join(g.label for g in unresolved)
        )

    labels = [g.label for g in groups]
    if len(labels) != len(set(labels)):
        raise ValueError(f"Duplicate birth-year labels detected: {labels}")

    return groups, gender_row, overall_col


def detect_data_start(df: pd.DataFrame, gender_row: int) -> int:
    for row_index in range(gender_row + 1, len(df)):
        value = structural_text(df.iat[row_index, 0])
        if value and "ΓΕΝΙΚΟ ΣΥΝΟΛΟ" in normalize(value):
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


def ttl_literal(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def ttl_birth_year(label: str) -> str:
    # 22AY uses grouped/boundary labels, so strings are the normal case.
    if re.fullmatch(r"\d{4}", label):
        return f'"{label}"^^<{XSD}gYear>'
    return f'{ttl_literal(label)}^^<{XSD}string>'


def academic_year_suffix(academic_year: str) -> str:
    match = re.fullmatch(r"AY(\d{4})_(\d{4})", academic_year)
    if not match:
        return academic_year
    return f"{match.group(1)}_{match.group(2)}"


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
) -> tuple[str, list[dict], list[dict], list[dict], dict, int]:
    filename = Path(path).name

    sheet_name, df = select_22ay_sheet(path)
    academic_year = detect_academic_year(df, filename)
    birth_groups, gender_row, overall_col = detect_birth_groups(df)
    data_start = detect_data_start(df, gender_row)

    header_audit = []
    for group in birth_groups:
        header_audit.append({
            "source_file": filename,
            "sheet": str(sheet_name),
            "academic_year": academic_year,
            "excel_total_column": group.total_col + 1,
            "raw_header": group.raw_header,
            "canonical_birth_label": group.label,
            "status": group.status,
        })

        if verbose and group.status != "DETECTED":
            print(
                f"    HEADER {group.status}: col {group.total_col + 1} | "
                f"{group.raw_header!r} -> {group.label}"
            )

    current_university = ""
    current_school = ""

    audit = []
    matched_uris: set[str] = set()
    ambiguous = 0
    inconsistencies = 0
    source_total_warnings = 0
    source_department_rows = 0
    data_audit: list[dict] = []

    # Aggregate split/historical source tracks resolving to the same mapping URI.
    # Key: (department URI, birth label)
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

        # Source overall Total/Male/Female triplet.
        overall_total = to_int(row.iloc[overall_col])
        overall_male = to_int(row.iloc[overall_col + 1])
        overall_female = to_int(row.iloc[overall_col + 2])

        if any(
            value is not None and value < 0
            for value in (overall_total, overall_male, overall_female)
        ):
            inconsistencies += 1
            if verbose:
                print(f"    NEGATIVE OVERALL row {row_index + 1}: {c2}")
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

        group_values = []

        for group in birth_groups:
            source_total = to_int(row.iloc[group.total_col])
            male = to_int(row.iloc[group.male_col])
            female = to_int(row.iloc[group.female_col])
            group_values.append((source_total, male, female))

            if source_total is None and male is None and female is None:
                continue

            if any(v is not None and v < 0 for v in (source_total, male, female)):
                inconsistencies += 1
                data_audit.append({
                    "source_file": filename,
                    "sheet": str(sheet_name),
                    "academic_year": academic_year,
                    "source_row": row_index + 1,
                    "source_department": c2,
                    "birth_group": group.label,
                    "issue": "NEGATIVE_VALUE",
                    "source_total": source_total,
                    "male": male,
                    "female": female,
                    "derived_total": "",
                })
                if verbose:
                    print(
                        f"    NEGATIVE row {row_index + 1}: {c2} | {group.label}"
                    )
                continue

            # In the older 22AY workbooks, the per-birth TOTAL cells can be
            # wrong while the Male/Female cells remain coherent. The log
            # validation showed that the gender-group sums still reconcile to
            # the overall Male/Female totals. Therefore:
            #
            #   * keep Male/Female as the actual observations;
            #   * derive the usable group total as Male + Female when both exist;
            #   * audit a conflicting source TOTAL instead of dropping the row.
            derived_total = (
                male + female
                if male is not None and female is not None
                else None
            )

            if (
                source_total is not None
                and derived_total is not None
                and source_total != derived_total
            ):
                source_total_warnings += 1
                data_audit.append({
                    "source_file": filename,
                    "sheet": str(sheet_name),
                    "academic_year": academic_year,
                    "source_row": row_index + 1,
                    "source_department": c2,
                    "birth_group": group.label,
                    "issue": "SOURCE_TOTAL_MISMATCH",
                    "source_total": source_total,
                    "male": male,
                    "female": female,
                    "derived_total": derived_total,
                })
                if verbose:
                    print(
                        f"    SOURCE TOTAL mismatch row {row_index + 1}: {c2} | "
                        f"{group.label}: source_total={source_total}, "
                        f"male={male}, female={female}, derived={derived_total}"
                    )

            key = (dept_uri, group.label)
            bucket = aggregate.setdefault(key, {
                "male": 0,
                "female": 0,
                "has_male": False,
                "has_female": False,
                "source_departments": set(),
            })

            if male is not None:
                bucket["male"] += male
                bucket["has_male"] = True
            if female is not None:
                bucket["female"] += female
                bucket["has_female"] = True

            bucket["source_departments"].add(c2)

        # Gender columns are the authoritative consistency check for 22AY.
        # The supplied validation log showed zero MALE/FEMALE birth-sum
        # mismatches in the problematic historical files, while only the
        # per-birth source TOTAL cells were inconsistent.
        totals = [values[0] for values in group_values]
        males = [values[1] for values in group_values]
        females = [values[2] for values in group_values]

        if overall_male is not None and males and all(v is not None for v in males):
            summed = sum(v for v in males if v is not None)
            if summed != overall_male:
                inconsistencies += 1
                data_audit.append({
                    "source_file": filename,
                    "sheet": str(sheet_name),
                    "academic_year": academic_year,
                    "source_row": row_index + 1,
                    "source_department": c2,
                    "birth_group": "ALL",
                    "issue": "MALE_BIRTH_SUM_MISMATCH",
                    "source_total": overall_total,
                    "male": overall_male,
                    "female": overall_female,
                    "derived_total": summed,
                })
                if verbose:
                    print(
                        f"    MALE BIRTH SUM mismatch row {row_index + 1}: {c2} | "
                        f"birth_groups={summed}, overall={overall_male}"
                    )

        if overall_female is not None and females and all(v is not None for v in females):
            summed = sum(v for v in females if v is not None)
            if summed != overall_female:
                inconsistencies += 1
                data_audit.append({
                    "source_file": filename,
                    "sheet": str(sheet_name),
                    "academic_year": academic_year,
                    "source_row": row_index + 1,
                    "source_department": c2,
                    "birth_group": "ALL",
                    "issue": "FEMALE_BIRTH_SUM_MISMATCH",
                    "source_total": overall_total,
                    "male": overall_male,
                    "female": overall_female,
                    "derived_total": summed,
                })
                if verbose:
                    print(
                        f"    FEMALE BIRTH SUM mismatch row {row_index + 1}: {c2} | "
                        f"birth_groups={summed}, overall={overall_female}"
                    )

        # Keep the historical source TOTAL-sum discrepancy for traceability,
        # but it is a source-data warning, not a reason to discard valid
        # Male/Female counts.
        if overall_total is not None and totals and all(v is not None for v in totals):
            summed = sum(v for v in totals if v is not None)
            if summed != overall_total:
                source_total_warnings += 1
                data_audit.append({
                    "source_file": filename,
                    "sheet": str(sheet_name),
                    "academic_year": academic_year,
                    "source_row": row_index + 1,
                    "source_department": c2,
                    "birth_group": "ALL",
                    "issue": "SOURCE_TOTAL_BIRTH_SUM_MISMATCH",
                    "source_total": overall_total,
                    "male": overall_male,
                    "female": overall_female,
                    "derived_total": summed,
                })
                if verbose:
                    print(
                        f"    SOURCE TOTAL birth-sum mismatch row {row_index + 1}: "
                        f"{c2} | birth_groups={summed}, overall={overall_total}"
                    )

    lines = []
    observations = 0

    for (dept_uri, birth_label), bucket in sorted(aggregate.items()):
        # Derive the denominator from the gender counts themselves. This avoids
        # propagating known-bad historical source TOTAL cells into percentages.
        total = (
            bucket["male"] + bucket["female"]
            if bucket["has_male"] and bucket["has_female"]
            else None
        )

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

            obs_uri = f"{RESOURCE}22AY_{academic_year}/obs_birth_{obs_counter}"
            ay_uri = f"{RESOURCE}{academic_year}"
            source_label = " + ".join(sorted(bucket["source_departments"]))

            lines.append("")
            lines.append(
                f"# {source_label[:120]} | {academic_year} | "
                f"birth {birth_label} | {gender}"
            )
            lines.append(f"<{obs_uri}>")
            lines.append(f"    a <{QB}Observation> ;")
            lines.append(f"    <{QB}dataSet> <{DATASET_URI}> ;")
            lines.append(f"    <{DIMENSION}academicYear> <{ay_uri}> ;")
            lines.append(f"    <{DIMENSION}department> <{dept_uri}> ;")
            lines.append(f"    <{DIMENSION}gender> <{RESOURCE}{gender}> ;")
            lines.append(f"    <{DIMENSION}birthYear> {ttl_birth_year(birth_label)} ;")
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

    repaired_headers = sum(
        1 for group in birth_groups
        if group.status in {
            "REPAIRED_SOURCE_TYPO",
            "INFERRED_BOUNDARY_FROM_ADJACENT_RANGE",
        }
    )

    header = (
        "# ============================================================\n"
        "# 22AY — External Teaching Staff by Birth Year and Gender\n"
        f"# Source: {filename}\n"
        f"# Source sheet: {sheet_name}\n"
        f"# Academic Year: {academic_year}\n"
        f"# Birth groups detected: {len(birth_groups)}\n"
        f"# Header labels repaired/inferred: {repaired_headers}\n"
        f"# Mapping departments found: {len(matched_uris)} / {len(mapping)}\n"
        f"# Observations generated: {observations}\n"
        "# ============================================================\n"
    )

    stats = {
        "academic_year": academic_year,
        "sheet": str(sheet_name),
        "birth_groups": len(birth_groups),
        "first_birth": birth_groups[0].label,
        "last_birth": birth_groups[-1].label,
        "repaired_headers": repaired_headers,
        "matched_departments": len(matched_uris),
        "source_department_rows": source_department_rows,
        "observations": observations,
        "ambiguous": ambiguous,
        "inconsistencies": inconsistencies,
        "source_total_warnings": source_total_warnings,
    }

    return (
        header + "\n".join(lines) + "\n",
        audit,
        header_audit,
        data_audit,
        stats,
        obs_counter,
    )


# ---------------------------------------------------------------------------
# CLI / output
# ---------------------------------------------------------------------------

def clean_output(out_dir: Path) -> None:
    if not out_dir.exists():
        return

    for pattern in (
        "22AY_*_cs.ttl",
        "22AY_all.ttl",
        "22AY_match_audit.csv",
        "22AY_header_audit.csv",
        "22AY_data_audit.csv",
    ):
        for path in out_dir.glob(pattern):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ELSTAT Table 22AY -> RDF Turtle using cs_schools.xlsx "
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

    out_dir = Path("rdf_output/22AY")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        clean_output(out_dir)

    if not args.quiet:
        print(f"ETL 22AY [{SCRIPT_VERSION}]")
        print(f"cs_schools whitelist: {len(mapping)} departments")

    all_rdf = []
    all_audit = []
    all_header_audit = []
    all_data_audit = []

    obs_counter = 0
    files_ok = 0
    files_failed = 0
    total_observations = 0
    total_ambiguous = 0
    total_inconsistencies = 0
    total_source_total_warnings = 0
    total_repairs = 0

    for number, path in enumerate(args.excel_files, start=1):
        filename = Path(path).name

        try:
            rdf, audit, header_audit, data_audit, stats, obs_counter = process_file(
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

        suffix = academic_year_suffix(stats["academic_year"])
        out_file = out_dir / f"22AY_{suffix}_cs.ttl"
        out_file.write_text(rdf, encoding="utf-8")

        all_rdf.append(rdf)
        all_audit.extend(audit)
        all_header_audit.extend(header_audit)
        all_data_audit.extend(data_audit)

        files_ok += 1
        total_observations += stats["observations"]
        total_ambiguous += stats["ambiguous"]
        total_inconsistencies += stats["inconsistencies"]
        total_source_total_warnings += stats["source_total_warnings"]
        total_repairs += stats["repaired_headers"]

        if not args.quiet:
            print(
                f"[{number}/{len(args.excel_files)}] {filename}: "
                f"sheet={stats['sheet']} | "
                f"birth {stats['first_birth']} ... {stats['last_birth']} | "
                f"{stats['birth_groups']} groups | "
                f"{stats['matched_departments']}/{len(mapping)} mapping depts | "
                f"{stats['observations']} obs | "
                f"{stats['repaired_headers']} header repair(s) | "
                f"{stats['source_total_warnings']} source-total warning(s) | "
                f"{stats['inconsistencies']} gender consistency error(s)"
            )

    combined = out_dir / "22AY_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")

    match_audit_file = out_dir / "22AY_match_audit.csv"
    audit_fields = [
        "source_file", "sheet", "academic_year", "source_row",
        "source_university", "source_school", "source_department",
        "mapping_id", "mapping_university", "mapping_school",
        "mapping_department", "mapping_uri",
        "university_score", "school_score", "department_score",
        "location_score", "coverage", "combined_score", "margin", "status",
    ]

    with match_audit_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        writer.writerows(all_audit)

    header_audit_file = out_dir / "22AY_header_audit.csv"
    header_fields = [
        "source_file", "sheet", "academic_year", "excel_total_column",
        "raw_header", "canonical_birth_label", "status",
    ]

    with header_audit_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header_fields)
        writer.writeheader()
        writer.writerows(all_header_audit)

    data_audit_file = out_dir / "22AY_data_audit.csv"
    data_fields = [
        "source_file", "sheet", "academic_year", "source_row",
        "source_department", "birth_group", "issue",
        "source_total", "male", "female", "derived_total",
    ]
    with data_audit_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=data_fields)
        writer.writeheader()
        writer.writerows(all_data_audit)

    print(
        f"Done: {files_ok}/{len(args.excel_files)} files | "
        f"{total_observations} observations | "
        f"{total_repairs} header repair/inference(s) | "
        f"{total_source_total_warnings} source-total warning(s) | "
        f"{total_inconsistencies} gender consistency error(s) | "
        "outside-whitelist URIs: 0"
    )

    if args.verbose:
        print(f"Ambiguous source department rows: {total_ambiguous}")
        print(f"Source-total warnings: {total_source_total_warnings}")
        print(f"Gender consistency errors: {total_inconsistencies}")
        print(f"Combined RDF: {combined}")
        print(f"Match audit: {match_audit_file}")
        print(f"Header audit: {header_audit_file}")
        print(f"Data audit: {data_audit_file}")

    return 1 if files_failed or total_ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())

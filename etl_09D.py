"""
ELSTAT Table 09D -> RDF Turtle
===============================

Doctoral/PhD students who received their doctoral degree, by birth year and gender.

This ETL processes ELSTAT Table 09D: doctoral/PhD graduates by birth year and gender.

Design:
- cs_schools.xlsx is the ONLY department whitelist.
- Department identity and URI always come from cs_schools.xlsx.
- The Table 09D sheet is selected dynamically from each workbook.
- Birth-year groups are reconstructed from the actual multi-row Excel header.
- Academic-year labels leaking into a legacy merged header (e.g. 2016/2017)
  are excluded before birth-year parsing.
- Exact years (e.g. 2002), grouped ranges, boundary groups and ΑΓΝΩΣΤΟ are supported.
- The 09D worksheet is discovered semantically and then loaded by sheet INDEX,
  avoiding Unicode sheet-name ambiguity in legacy .xls files.
- The gender row is selected by birth-year semantics, not by the maximum number
  of Total/Male/Female triplets.
- No hardcoded birth years, column indexes, header rows, or data-start rows.
- Handles normal 5-year ranges, "XXXX και μετά", "XXXX και πριν", and unknown.
- Safely repairs a clearly identifiable one-character source typo in a 5-year
  range (e.g. 19634-1968 -> 1964-1968) and records the repair in a header audit.
- Supports .xls and .xlsx.
- Zero-count observations are omitted by default.
- Matching diagnostics go to 09D_match_audit.csv.
- Header-label repairs/detection diagnostics go to 09D_header_audit.csv.
- Internal consistency checks verify Male + Female = Total and, when complete,
  that the sum of all birth groups equals the source overall Total/Male/Female.
- Source overall totals are cross-checked against all detected birth-year groups.

Usage:
    python3 etl_09D.py --clean cs_schools.xlsx data/09D/*.xls data/09D/*.xlsx

Options:
    --clean         remove previous Table 09D generated outputs
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

SCRIPT_VERSION = "09D-dynamic-mapping-v1-2026-09-04"
TABLE_CODE = "09D"
PROGRAM_TYPE = "PhD"

BASE      = "http://www.ics.forth.gr/uniStats/"
RESOURCE  = BASE + "resource/"
DIMENSION = BASE + "dimension/"
DATASET   = BASE + "dataset/"
QB        = "http://purl.org/linked-data/cube#"
XSD       = "http://www.w3.org/2001/XMLSchema#"

DATASET_URI = DATASET + "graduatesByBirthYearDataset"

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
# Workbook / 09D sheet selection
# ---------------------------------------------------------------------------

def excel_engine(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return "xlrd"
    if suffix == ".xlsx":
        return "openpyxl"
    raise ValueError(f"Unsupported file type: {suffix}")


def select_09d_sheet(path: str) -> tuple[object, pd.DataFrame]:
    """
    Select ELSTAT Table 09D: doctoral/PhD graduates by birth year.

    Legacy .xls files are read back by numeric sheet index after discovery to
    avoid Unicode/Greek-Latin sheet-name ambiguity.
    """
    engine = excel_engine(path)
    book = pd.ExcelFile(path, engine=engine)
    candidates = []

    for sheet_index, sheet_name in enumerate(book.sheet_names):
        preview = pd.read_excel(
            book,
            sheet_name=sheet_index,
            header=None,
            nrows=35,
            dtype=object,
        )

        joined = " ".join(
            normalize(value)
            for value in preview.to_numpy().ravel()
            if not pd.isna(value)
        )
        raw_sheet = str(sheet_name).upper().replace(" ", "")
        norm_sheet = normalize(sheet_name).replace(" ", "")

        score = 0

        # Typical names in this family include 9DY / 09D / 09DL.
        if (
            "09D" in raw_sheet
            or "9D" in raw_sheet
            or "09D" in norm_sheet
            or "9D" in norm_sheet
        ):
            score += 50

        if "ΠΙΝΑΚΑΣ 9" in joined or "ΠΙΝΑΚΑΣ 09" in joined:
            score += 60

        # Core semantic signals.
        if "ΠΤΥΧ" in joined:
            score += 80
        if "ΔΙΔΑΚΤΟΡΙΚ" in joined:
            score += 140
        if "ΓΕΝΝΗΣ" in joined:
            score += 90
        if "ΦΟΙΤΗΤ" in joined:
            score += 20

        # Guard against accidentally selecting the MSc counterpart if a
        # workbook ever contains multiple related sheets.
        if "ΜΕΤΑΠΤΥΧ" in joined and "ΔΙΔΑΚΤΟΡΙΚ" not in joined:
            score -= 120

        candidates.append({
            "score": score,
            "sheet_index": sheet_index,
            "sheet_name": sheet_name,
        })

    if not candidates:
        raise ValueError("Workbook contains no sheets")

    candidates.sort(
        key=lambda item: (item["score"], -item["sheet_index"]),
        reverse=True,
    )
    best = candidates[0]

    if best["score"] < 230:
        detail = ", ".join(
            f"{c['sheet_index']}:{c['sheet_name']} score={c['score']}"
            for c in candidates
        )
        raise ValueError(
            "Could not confidently identify the Table 09D "
            "PhD-graduates-by-birth-year sheet. "
            f"Sheets: {detail}"
        )

    df = pd.read_excel(
        path,
        sheet_name=best["sheet_index"],
        engine=engine,
        header=None,
        dtype=object,
    )

    first_rows_text = " ".join(
        normalize(value)
        for value in df.iloc[:35].to_numpy().ravel()
        if not pd.isna(value)
    )

    if (
        "ΠΤΥΧ" not in first_rows_text
        or "ΔΙΔΑΚΤΟΡΙΚ" not in first_rows_text
        or "ΓΕΝΝΗΣ" not in first_rows_text
    ):
        raise ValueError(
            f"Sheet-selection integrity failure for {best['sheet_name']!r}: "
            "expected PhD-graduate/birth-year semantics were not found."
        )

    return best["sheet_name"], df

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
    """
    Detect the repeated ΣΥΝΟΛΟ / ΑΡΡΕΝΕΣ / ΘΗΛΕΙΣ header row.

    We score candidates by how many of the triplets above them resolve to
    genuine birth-year labels, rather than merely taking the widest row.
    """
    candidates = []

    for row_index in range(min(50, len(df))):
        triplet_cols = []

        for col in range(max(0, df.shape[1] - 2)):
            if (
                is_total_header(df.iat[row_index, col])
                and is_male_header(df.iat[row_index, col + 1])
                and is_female_header(df.iat[row_index, col + 2])
            ):
                triplet_cols.append(col)

        if len(triplet_cols) < 2:
            continue

        overall_col = min(triplet_cols)
        birth_cols = [col for col in triplet_cols if col != overall_col]

        valid_birth_headers = 0
        invalid_birth_headers = 0

        for col in birth_cols:
            raw = reconstruct_triplet_header(df, row_index, col)
            label, _status = parse_birth_header_text(raw)
            if label is None:
                invalid_birth_headers += 1
            else:
                valid_birth_headers += 1

        score = (
            valid_birth_headers * 25
            + len(triplet_cols)
            - invalid_birth_headers * 30
        )

        candidates.append({
            "score": score,
            "row": row_index,
            "triplets": len(triplet_cols),
            "valid": valid_birth_headers,
            "invalid": invalid_birth_headers,
        })

    if not candidates:
        raise ValueError(
            "Could not detect the Table 09D Total/Male/Female header row."
        )

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["valid"],
            -item["invalid"],
            item["triplets"],
        ),
        reverse=True,
    )
    best = candidates[0]

    if best["valid"] < 3:
        detail = ", ".join(
            f"row {c['row'] + 1}: {c['triplets']} triplets, {c['valid']} birth labels"
            for c in candidates[:5]
        )
        raise ValueError(
            "Could not confidently identify the Table 09D birth-year gender row. "
            f"Candidates: {detail}"
        )

    return best["row"]

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
    Parse a Table 09D birth-year header.

    Valid forms:
      2002
      1969-1973
      1958 ΚΑΙ ΠΡΙΝ
      2003 ΚΑΙ ΜΕΤΑ
      ΑΓΝΩΣΤΟ

    A clearly repairable one-character typo inside a 5-year range is corrected
    and recorded as REPAIRED_SOURCE_TYPO.
    """
    raw = _strip_accents_upper(raw_header)
    raw = raw.replace("–", "-").replace("—", "-")
    raw = re.sub(r"\s+", " ", raw).strip()

    norm = normalize(raw)

    if "ΑΓΝΩΣΤ" in norm:
        return "AGNOSTO", "DETECTED"

    # A legacy header can leak the academic-year text (e.g. 2016/2017)
    # into the reconstructed header of the first birth-year triplet.
    # Remove such consecutive academic-year pairs before parsing the actual
    # birth-year label. This prevents 2016/2017 from being mistaken for a
    # birth-year range in the 2016-17 workbook.
    raw = re.sub(
        r"(?<!\d)(20\d{2})\s*[/\-]\s*(20\d{2})(?!\d)",
        lambda m: " " if int(m.group(2)) == int(m.group(1)) + 1 else m.group(0),
        raw,
    )

    numbers = re.findall(r"(?<!\d)(\d{4,5})(?!\d)", raw)

    if len(numbers) >= 2:
        left_raw, right_raw = numbers[0], numbers[1]

        if len(left_raw) == 4 and len(right_raw) == 4:
            left, right = int(left_raw), int(right_raw)
            if 1900 <= left <= 2100 and 1900 <= right <= 2100:
                lo, hi = sorted((left, right))
                return f"{lo}-{hi}", "DETECTED"

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

        # Table 09D legitimately has many
        # single exact-year columns. Do NOT infer these as boundary labels.
        return str(year), "DETECTED"

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
            "Could not safely identify every 09D birth-year group: " + details
        )

    if not groups:
        raise ValueError("No 09D birth-year groups detected")

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
    # 09D uses grouped/boundary labels, so strings are the normal case.
    if re.fullmatch(r"\d{4}", label):
        return f'"{label}"^^<{XSD}gYear>'
    return f'{ttl_literal(label)}^^<{XSD}string>'



def make_program_uri(dept_uri: str) -> str:
    """
    Preserve the exact department URI from cs_schools.xlsx and append #BSc.

    Example:
        https://www.csd.uoc.gr/ -> https://www.csd.uoc.gr/#PhD
    """
    return f"{dept_uri}#{PROGRAM_TYPE}"

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
) -> tuple[str, list[dict], list[dict], dict, int]:
    filename = Path(path).name

    sheet_name, df = select_09d_sheet(path)
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
    source_department_rows = 0

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
            total = to_int(row.iloc[group.total_col])
            male = to_int(row.iloc[group.male_col])
            female = to_int(row.iloc[group.female_col])
            group_values.append((total, male, female))

            if total is None and male is None and female is None:
                continue

            if any(v is not None and v < 0 for v in (total, male, female)):
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
                continue

            key = (dept_uri, group.label)
            bucket = aggregate.setdefault(key, {
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

        # Cross-check all birth groups against the source overall triplet when
        # every component is available. Unknown is included if present.
        totals = [values[0] for values in group_values]
        males = [values[1] for values in group_values]
        females = [values[2] for values in group_values]

        if overall_total is not None and totals and all(v is not None for v in totals):
            summed = sum(v for v in totals if v is not None)
            if summed != overall_total:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    BIRTH SUM mismatch row {row_index + 1}: {c2} | "
                        f"birth_groups={summed}, overall={overall_total}"
                    )

        if overall_male is not None and males and all(v is not None for v in males):
            summed = sum(v for v in males if v is not None)
            if summed != overall_male:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    MALE BIRTH SUM mismatch row {row_index + 1}: {c2} | "
                        f"birth_groups={summed}, overall={overall_male}"
                    )

        if overall_female is not None and females and all(v is not None for v in females):
            summed = sum(v for v in females if v is not None)
            if summed != overall_female:
                inconsistencies += 1
                if verbose:
                    print(
                        f"    FEMALE BIRTH SUM mismatch row {row_index + 1}: {c2} | "
                        f"birth_groups={summed}, overall={overall_female}"
                    )

    lines = []
    observations = 0

    for (dept_uri, birth_label), bucket in sorted(aggregate.items()):
        total = bucket["total"] if bucket["has_total"] else None
        program_uri = make_program_uri(dept_uri)

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

            obs_uri = f"{RESOURCE}09D_{academic_year}/obs_birth_{obs_counter}"
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
            lines.append(f"    <{DIMENSION}program> <{program_uri}> ;")
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
        }
    )

    header = (
        "# ============================================================\n"
        "# 09D — PhD Graduates by Birth Year and Gender\n"
        f"# Source: {filename}\n"
        f"# Source sheet: {sheet_name}\n"
        f"# Academic Year: {academic_year}\n"
        f"# Birth groups detected: {len(birth_groups)}\n"
        f"# Header labels repaired/inferred: {repaired_headers}\n"
        f"# Mapping departments found: {len(matched_uris)} / {len(mapping)}\n"
        f"# Observations generated: {observations}\n"
        "# ============================================================\n"
    )

    non_unknown_groups = [
        group for group in birth_groups
        if group.label != "AGNOSTO"
    ]

    stats = {
        "academic_year": academic_year,
        "sheet": str(sheet_name),
        "birth_groups": len(birth_groups),
        "first_birth": non_unknown_groups[0].label if non_unknown_groups else birth_groups[0].label,
        "last_birth": non_unknown_groups[-1].label if non_unknown_groups else birth_groups[-1].label,
        "has_unknown": any(group.label == "AGNOSTO" for group in birth_groups),
        "repaired_headers": repaired_headers,
        "matched_departments": len(matched_uris),
        "source_department_rows": source_department_rows,
        "observations": observations,
        "ambiguous": ambiguous,
        "inconsistencies": inconsistencies,
    }

    return (
        header + "\n".join(lines) + "\n",
        audit,
        header_audit,
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
        "09D_*_cs.ttl",
        "09D_all.ttl",
        "09D_match_audit.csv",
        "09D_header_audit.csv",
    ):
        for path in out_dir.glob(pattern):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ELSTAT Table 09D -> RDF Turtle using cs_schools.xlsx "
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

    out_dir = Path("rdf_output/09D")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        clean_output(out_dir)

    if not args.quiet:
        print(f"ETL 09D [{SCRIPT_VERSION}]")
        print(f"cs_schools whitelist: {len(mapping)} departments")

    all_rdf = []
    all_audit = []
    all_header_audit = []

    obs_counter = 0
    files_ok = 0
    files_failed = 0
    total_observations = 0
    total_ambiguous = 0
    total_inconsistencies = 0
    total_repairs = 0

    for number, path in enumerate(args.excel_files, start=1):
        filename = Path(path).name

        try:
            rdf, audit, header_audit, stats, obs_counter = process_file(
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
        out_file = out_dir / f"09D_{suffix}_cs.ttl"
        out_file.write_text(rdf, encoding="utf-8")

        all_rdf.append(rdf)
        all_audit.extend(audit)
        all_header_audit.extend(header_audit)

        files_ok += 1
        total_observations += stats["observations"]
        total_ambiguous += stats["ambiguous"]
        total_inconsistencies += stats["inconsistencies"]
        total_repairs += stats["repaired_headers"]

        if not args.quiet:
            print(
                f"[{number}/{len(args.excel_files)}] {filename}: "
                f"sheet={stats['sheet']} | "
                f"birth {stats['first_birth']} ... {stats['last_birth']} | "
                f"{stats['birth_groups']} groups"
                f"{' (+unknown)' if stats.get('has_unknown') else ''} | "
                f"{stats['matched_departments']}/{len(mapping)} mapping depts | "
                f"{stats['observations']} obs | "
                f"{stats['repaired_headers']} header repair(s) | "
                f"{stats['inconsistencies']} consistency warning(s)"
            )

    combined = out_dir / "09D_all.ttl"
    combined.write_text("\n\n".join(all_rdf), encoding="utf-8")

    match_audit_file = out_dir / "09D_match_audit.csv"
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

    header_audit_file = out_dir / "09D_header_audit.csv"
    header_fields = [
        "source_file", "sheet", "academic_year", "excel_total_column",
        "raw_header", "canonical_birth_label", "status",
    ]

    with header_audit_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header_fields)
        writer.writeheader()
        writer.writerows(all_header_audit)

    print(
        f"Done: {files_ok}/{len(args.excel_files)} files | "
        f"{total_observations} observations | "
        f"{total_repairs} header repair/inference(s) | "
        "outside-whitelist URIs: 0"
    )

    if args.verbose:
        print(f"Ambiguous source department rows: {total_ambiguous}")
        print(f"Data consistency warnings: {total_inconsistencies}")
        print(f"Combined RDF: {combined}")
        print(f"Match audit: {match_audit_file}")
        print(f"Header audit: {header_audit_file}")

    return 1 if files_failed or total_ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())

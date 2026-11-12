"""The 41 corrected thesis queries, with typed filters and no arbitrary query endpoint."""
import re
from .config import ROOT, BASE

QUERIES = {p.stem: p.read_text(encoding="utf-8") for p in (ROOT / "queries").glob("*.rq")}

TITLES = {
    "00_dataset_inventory": "Dataset inventory", "01_school_metadata": "Departments and programs",
    "04_city_region": "Student population by city and region", "04_gender_share": "Student gender representation",
    "04_master_filterable": "Undergraduate students by study year", "04_nplus_gender_gap": "Beyond normal duration, by gender",
    "04_program_trend": "One undergraduate program over time", "04_sai": "Study accumulation index",
    "04_studyyear_values": "Study-year vocabulary", "05_average_age": "Average postgraduate age",
    "05_city_region_age": "Postgraduate age by location", "05_master_filterable": "Postgraduate students by birth year",
    "05_msc_phd_gender": "MSc and PhD gender representation", "06_average_age": "Average undergraduate age",
    "06_freshmen_non18": "First-year students outside age 18", "06_ger": "Female representation at entry",
    "06_master_filterable": "Undergraduates by birth and study year", "08_city_region_grade": "Graduate grades by location",
    "08_gender_percentage": "Graduate gender representation", "08_grade_percentage": "Degree-grade distribution",
    "08_grade_values": "Degree-grade vocabulary", "08_master_filterable": "Graduates by degree grade",
    "09_average_age": "Average graduate age", "09_city_region_age": "Graduate age by location",
    "09_master_filterable": "Graduates by birth year", "21_broader_categories": "Teaching staff by broader category",
    "21_master_filterable": "Internal teaching staff", "21_staff_category": "Teaching staff by category",
    "22_master_filterable": "External teaching staff", "X01_cohort_2020_2024": "Synthetic completion, 2020–2024",
    "X02_cohort_general": "Synthetic completion across cohorts", "X03_delta_otgr": "Gender completion gap",
    "X04_students_per_staff": "Students per teaching staff member", "X05_students_per_dep": "Students per DEP member",
    "X06_gapri": "Gender academic pipeline representation", "X07_pgr": "Postgraduate transition proxy",
    "X08_dii": "Doctoral intensity", "X09_gpr": "Postgraduate population ratio",
    "X10_external_staff_reliance": "External teaching staff reliance", "G01_sai_by_city_region": "Study accumulation by location",
    "G02_gender_by_city_region": "Female representation by location",
}

DESCRIPTIONS = {
    "04_sai": "SAI is the percentage of enrolled students studying beyond their program's normal duration. Compare it with the underlying student counts; it is not an individual dropout measure.",
    "04_program_trend": "Enrollment over time for the University of Crete undergraduate program specified in this thesis query. Missing years have no returned observation.",
    "04_nplus_gender_gap": "Compare the share studying beyond normal duration within each gender and program.",
    "06_ger": "Female students as a percentage of the observed first-year undergraduate population.",
    "06_freshmen_non18": "The percentage of first-year students whose exact birth year implies an age other than 18 at the start of the academic year. Unknown or grouped birth years are excluded.",
    "X01_cohort_2020_2024": "Compare 2023–2024 graduates born in 2002 with 2020–2021 first-year entrants born in 2002, for four-year programs. SOTGR = graduates / entrants × 100. This is an aggregate proxy, not individually tracked completion.",
    "X02_cohort_general": "The same synthetic completion ratio across four available entry/graduation-year pairs in four-year programs. SOTGR = graduates / entrants × 100. These are aggregate comparisons, not individual student histories.",
    "X03_delta_otgr": "The female synthetic completion percentage minus the male percentage, in percentage points, for the 2020–2024 four-year comparison. Positive values indicate a higher female ratio; this does not track individual progression.",
    "X04_students_per_staff": "Undergraduate enrollment divided by internal teaching staff in the same department and year. Only departments with matching student and staff observations appear.",
    "X05_students_per_dep": "Undergraduate enrollment divided by DEP faculty members in the same department and year. DEP counts use the direct child categories in the supplied staff hierarchy.",
    "X06_gapri": "Compare female representation among PhD and undergraduate students. GAPRI = PhD female share / undergraduate female share × 100; 100 means equal representation. The change column reports percentage points. This is a population comparison, not a tracked pipeline.",
    "X07_pgr": "MSc enrollment divided by undergraduate graduates in the same year, multiplied by 100. This compares a population with an annual graduate count; it is not a measured transition probability and can exceed 100.",
    "X08_dii": "PhD enrollment divided by internal teaching staff in the same department and year. DII is doctoral students per staff member, not a percentage.",
    "X09_gpr": "Postgraduate enrollment divided by undergraduates within normal program duration, multiplied by 100. Undergraduate N+ students are excluded from the denominator. This is a population ratio, not an individual transition rate.",
    "X10_external_staff_reliance": "External teaching assignments as a percentage of internal plus external assignments. Only department-years represented in both datasets appear; missing external observations are not assumed to be zero.",
    "G01_sai_by_city_region": "The percentage of enrolled students beyond normal duration, aggregated by city and region. Larger departments contribute proportionally to the result.",
    "G02_gender_by_city_region": "Female representation in undergraduate enrollment, aggregated by city and region. Larger departments contribute proportionally to the result.",
}


def description(key):
    if key in DESCRIPTIONS:
        return DESCRIPTIONS[key]
    if key.endswith("_age"):
        return "Weighted mean age using exact birth years and the academic year's starting year. Grouped and unknown birth years are excluded. These queries do not estimate an exact age at an individual event."
    if key.startswith("05"):
        return "Explore MSc and PhD students only; BSc observations in dataset 05 are excluded. Choose a study level where the query supports it."
    return "Explore the corrected thesis query with the available filters. Missing results indicate no matching observations and are not automatically measured zeros."


def entries():
    return [{"id": key, "title": TITLES.get(key, key), "group": group(key),
             "filters": available_filters(value), "description": description(key), "query": value} for key, value in sorted(QUERIES.items())]


def group(key):
    prefix = key.split("_")[0]
    if prefix.startswith("X"): return "Cross-dataset indicators"
    if prefix.startswith("G"): return "Geography"
    return {"00": "Metadata", "01": "Metadata", "04": "Undergraduates", "05": "Postgraduates",
            "06": "Entry and age", "08": "Graduate grades", "09": "Graduate age",
            "21": "Internal staff", "22": "External staff"}.get(prefix, "Other")


def available_filters(query):
    return [field for field in ("academicYear", "department", "gender", "programType")
            if re.search(r"# VALUES \?" + field + r"\b", query)
            or (field == "programType" and "VALUES ?programType { res:MSc res:PhD }" in query)]


def filtered_query(query_id, filters, departments):
    if query_id not in QUERIES:
        raise ValueError("Unknown catalogue query.")
    query = QUERIES[query_id]
    allowed = available_filters(query)
    clauses = []
    for key, value in filters.items():
        if value in (None, "", "all"): continue
        if key not in allowed: raise ValueError(f"This query does not support the {key} filter.")
        if key == "academicYear":
            if not re.fullmatch(r"AY20\d{2}_20\d{2}", value): raise ValueError("Invalid academic year.")
            term = "res:" + value
        elif key == "gender":
            if value not in {"Male", "Female"}: raise ValueError("Invalid gender.")
            term = "res:" + value
        elif key == "programType":
            if value not in {"MSc", "PhD"}: raise ValueError("Choose MSc or PhD.")
            term = "res:" + value
            query = query.replace("VALUES ?programType { res:MSc res:PhD }", f"VALUES ?programType {{ {term} }}")
        else:
            if value not in departments: raise ValueError("Unknown department.")
            term = f"<{value}>"
        clauses.append(f"  VALUES ?{key} {{ {term} }}")
    if clauses:
        query = re.sub(r"WHERE\s*\{", lambda m: m.group(0) + "\n" + "\n".join(clauses), query, count=1)
    return query

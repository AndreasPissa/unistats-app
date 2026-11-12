"""Explorer queries and aggregation. Missing groups are never fabricated as zero."""
from collections import defaultdict
from .config import PREFIXES, FROM, BASE

DATASETS = {
    "students": {"label": "Undergraduate students", "table": "04", "dataset": "studentsByStudyYearDataset", "dimension": "studyYear", "unit": "students", "metrics": ["total", "femaleShare", "beyondShare"], "note": "N+ means beyond the normal duration of the program, which can be four or five years."},
    "postgraduates": {"label": "Postgraduate students", "table": "05", "dataset": "studentsByBirthYearDataset", "dimension": None, "unit": "students", "metrics": ["total", "femaleShare"], "note": "Includes MSc and PhD students only. Undergraduate records in dataset 05 are excluded."},
    "graduates": {"label": "Undergraduate graduates", "table": "08", "dataset": "graduatesByDegreeGradeDataset", "dimension": "degreeGrade", "unit": "graduates", "metrics": ["total", "femaleShare", "excellentShare"], "note": "Graduate totals sum Good, Very good and Excellent degree classifications."},
    "staff": {"label": "Internal teaching staff", "table": "21", "dataset": "teachingStaffByCategoryDataset", "dimension": "staffCategory", "unit": "staff", "metrics": ["total", "femaleShare"], "note": "Staff belonging to and teaching in the department. Detailed categories are summed once; parent totals are not added."},
    "external": {"label": "External teaching staff", "table": "22", "dataset": "externalTeachingStaffByCategoryDataset", "dimension": "staffCategory", "unit": "staff", "metrics": ["total", "femaleShare"], "note": "Staff teaching in a department while belonging to another. Totals are departmental assignments, not deduplicated people."},
    "entry": {"label": "Undergraduate entry", "table": "06", "dataset": "studentsByBirthYearStudyYearDataset", "dimension": "studyYear", "unit": "students", "metrics": ["total", "femaleShare"], "note": "First-year students across all birth-year groups. This is an entry population, not a tracked individual cohort."},
}
METRICS = {"total": "Population", "femaleShare": "Female share", "beyondShare": "Beyond normal duration", "excellentShare": "Excellent degree share"}


def population_config(dataset, birth_year=None):
    config = dict(DATASETS[dataset])
    if birth_year is not None and dataset == "students":
        config.update(dataset="studentsByBirthYearStudyYearDataset", table="06",
                      note="Birth-year filtering uses table 06 (birth and study year), whose coverage differs from the study-year-only table 04. Grouped birth years remain groups.")
    if birth_year is not None and dataset == "graduates":
        config.update(dataset="graduatesByBirthYearDataset", table="09", dimension=None, metrics=["total", "femaleShare"],
                      note="Birth-year filtering uses table 09. Degree grade and birth year are separate aggregate tables and cannot be combined. Grouped birth years remain groups.")
    return config


def staff_detail_clause(dataset):
    # Prefer the most detailed recorded category for each department/year/gender.
    return f"""MINUS {{ SELECT DISTINCT ?department ?academicYear ?gender ?category WHERE {{
      ?detail qb:dataSet <{BASE}dataset/{DATASETS[dataset]['dataset']}> ;
        dim:department ?department ; dim:academicYear ?academicYear ; dim:gender ?gender ;
        dim:staffCategory ?child ; dim:count ?detailCount .
      ?child skos:broader+ ?category . FILTER(?child != ?category)
    }} }}"""


def explorer_query(dataset, birth_year=None):
    config = population_config(dataset, birth_year)
    staff = dataset in {"staff", "external"}
    institution = "dim:department ?department ;" if staff else "dim:program ?program ;"
    category = f"?o dim:{config['dimension']} ?category ." if config["dimension"] else "BIND(?programType AS ?category)"
    program = 'BIND("Staff" AS ?programType)' if staff else "?department uni:hasUniversityProgram ?program . ?program uni:hasUniversityProgramType ?programType ."
    scope = "VALUES ?programType { res:MSc res:PhD }" if dataset == "postgraduates" else ""
    if not staff and dataset != "postgraduates": scope += "\n  VALUES ?programType { res:BSc }"
    if dataset == "entry": scope += "\n  VALUES ?category { res:StudyYear1 }"
    if staff: scope += "\n" + staff_detail_clause(dataset)
    birth = "OPTIONAL { ?o dim:birthYear ?birthYear }" if dataset in {"postgraduates", "entry"} or birth_year is not None else ""
    return PREFIXES + f"""
SELECT ?academicYear ?department ?programType ?gender ?category ?birthYear (SUM(?n) AS ?count)
{FROM}
WHERE {{
  ?o qb:dataSet <{BASE}dataset/{config['dataset']}> ;
     dim:academicYear ?academicYear ; {institution} dim:gender ?gender ; dim:count ?n .
  {program}
  {category}
  {scope}
  {birth}
}}
GROUP BY ?academicYear ?department ?programType ?gender ?category ?birthYear
ORDER BY ?academicYear ?department ?category ?gender ?birthYear"""


def short(uri): return uri.rsplit("/", 1)[-1]


def summarize(rows):
    total = sum(r["count"] for r in rows)
    female = sum(r["count"] for r in rows if r["gender"] == "Female")
    male = sum(r["count"] for r in rows if r["gender"] == "Male")
    beyond = sum(r["count"] for r in rows if r["category"] == "BeyondNormalYears")
    excellent = sum(r["count"] for r in rows if r["category"] == "Excellent")
    return {"total": total, "female": female, "male": male, "beyond": beyond,
            "femaleShare": round(female / total * 100, 2) if total else None,
            "beyondShare": round(beyond / total * 100, 2) if total else None,
            "excellentShare": round(excellent / total * 100, 2) if total else None,
            "departments": len({r["department"] for r in rows}), "observedGroups": len(rows)}


def explore(rows, metadata, *, dataset, year=None, university=None, department=None, region=None, city=None,
            gender=None, level=None, category=None, staff_group=None, birth_year=None, category_detail="broad",
            metric="total", group_by="department", top=10):
    config = population_config(dataset, birth_year)
    if metric not in config["metrics"] or (birth_year is not None and dataset == "graduates" and category):
        raise ValueError("Birth year and degree grade come from separate tables and cannot be combined.")
    if birth_year is not None and dataset in {"staff", "external"}:
        raise ValueError("Teaching staff observations do not include birth year.")
    if staff_group and dataset not in {"staff", "external"}:
        raise ValueError("Broad staff categories apply only to teaching staff.")
    groups = {g["id"]: g for g in metadata.get("staffGroups", [])}
    if staff_group and staff_group not in groups:
        raise ValueError("Unknown broad staff category.")
    dept_map = {d["uri"]: d for d in metadata["departments"]}
    normalized = [{**r, "year": short(r["academicYear"]), "gender": short(r["gender"]),
                   "category": short(r["category"]), "level": short(r["programType"]), "count": int(r["count"])} for r in rows]
    years = sorted({r["year"] for r in normalized})
    chosen = year or (years[-1] if years else None)
    if chosen and chosen not in years: raise ValueError("This dataset has no observations for that academic year.")
    categories_available = sorted({r["category"] for r in normalized})
    birth_available = metadata.get("vocabulary", {}).get(dataset, {}).get("birthYears", [])
    if birth_year is not None and birth_year not in birth_available:
        raise ValueError("Choose a birth year or birth-year group available in this dataset.")
    if category and category not in categories_available:
        raise ValueError("Choose a category available in this dataset.")
    filtered = []
    for r in normalized:
        d = dept_map.get(r["department"])
        if not d: continue
        if university and d["university"] != university: continue
        if department and r["department"] != department: continue
        if region and d["region"] != region: continue
        if city and d["city"] != city: continue
        if gender and r["gender"] != gender: continue
        if level and r["level"] != level: continue
        if category and r["category"] != category: continue
        if staff_group and r["category"] not in groups[staff_group]["members"]: continue
        if birth_year is not None and r.get("birthYear") != birth_year: continue
        filtered.append(r)
    current = [r for r in filtered if r["year"] == chosen]
    ranking = defaultdict(list)
    distribution = defaultdict(list)
    for row in current:
        d = dept_map[row["department"]]
        key = row["department"] if group_by == "department" else d[group_by]
        ranking[key].append(row)
        broad = [g["id"] for g in groups.values() if row["category"] in g["members"]]
        # Multiple broader paths must not multiply the population.
        key = broad[0] if len(broad) == 1 else row["category"]
        distribution_key = key if dataset in {"staff", "external"} and category_detail == "broad" else row["category"]
        if dataset == "graduates" and birth_year is not None:
            distribution_key = row["gender"]
        distribution[distribution_key].append(row)
    ranked = []
    for key, group in ranking.items():
        d = dept_map[group[0]["department"]]
        label = d["label"] if group_by == "department" else d[group_by + "Label"]
        summary = summarize(group)
        ranked.append({"id": key, "label": label, "university": d["universityLabel"] if group_by == "department" else "",
                       "city": d["cityLabel"] if group_by == "department" else "", **summary, "value": summary[metric]})
    ranked.sort(key=lambda r: (-(r["value"] or 0), r["label"]))
    trend = []
    # Missing academic years are explicit nulls so line charts do not bridge gaps.
    if years:
        for y in range(int(years[0][2:6]), int(years[-1][2:6]) + 1):
            ay = f"AY{y}_{y+1}"
            group = [r for r in filtered if r["year"] == ay]
            summary = summarize(group) if group else None
            trend.append({"year": ay, "value": summary[metric] if summary else None, "total": summary["total"] if summary else None})
    categories = [{"id": key, "count": sum(r["count"] for r in group)} for key, group in distribution.items()]
    categories.sort(key=lambda r: (-r["count"], r["id"]))
    return {"dataset": dataset, "year": chosen, "availableYears": years, "metric": metric,
            "metricLabel": METRICS[metric], "unit": DATASETS[dataset]["unit"], "groupBy": group_by,
            "summary": summarize(current), "ranking": ranked[:top], "allRows": ranked,
            "trend": trend, "distribution": categories, "note": config["note"], "table": config["table"],
            "categoryDetail": category_detail, "birthYear": birth_year,
            "availableBirthYears": birth_available,
            "availableStaffGroups": [g for g in groups.values() if set(g["members"]) & set(categories_available)],
            "availableCategories": [c for c in categories_available if not staff_group or c in groups[staff_group]["members"]],
            "filtered": bool(university or department or region or city or gender or level or category or staff_group or birth_year)}

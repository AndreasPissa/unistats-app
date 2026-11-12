"""Typed AI plans compiled into server-owned, read-only SPARQL."""
import re
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analytics import DATASETS, METRICS, population_config, staff_detail_clause, short
from .catalogue import entries, filtered_query, description
from .config import BASE, PREFIXES, FROM


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=2000)
    previous_question: str | None = Field(default=None, max_length=2000)


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["statistics", "catalogue", "clarify"]
    dataset: Literal["students", "postgraduates", "graduates", "staff", "external", "entry"] = "students"
    metric: Literal["total", "femaleShare", "beyondShare", "excellentShare"] = Field(default="total", description="Use total for 'how many', counts, enrollment or population. Share metrics ONLY when explicitly asked for a percentage, share, proportion or representation.")
    group_by: Literal["total", "department", "university", "city", "region", "gender", "year", "level", "category", "staff_group", "birth_year"] = "total"
    years: list[str] = Field(default_factory=list, max_length=100)
    universities: list[str] = Field(default_factory=list, max_length=500)
    departments: list[str] = Field(default_factory=list, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    gender: Literal["Female", "Male"] | None = None
    level: Literal["MSc", "PhD"] | None = None
    category: str | None = Field(default=None, max_length=100, description="Null unless a specific study-year, degree-grade or staff category is requested. A population name such as students is NOT a category.")
    staff_group: str | None = Field(default=None, max_length=100, description="Broad teaching-staff group ID from context, e.g. DEP is RegularTeachingResearchStaff. Specific rank goes in category.")
    birth_year: str | None = Field(default=None, max_length=100, description="Exact stored birth-year value from context. Preserve ranges, earlier-year groups and unknowns; do not infer individual years inside groups.")
    top: int | None = Field(default=None, ge=1, le=50)
    query_id: str | None = Field(default=None, max_length=80)
    message: str = Field(default="", max_length=600)


class Clarification(ValueError):
    """The request cannot be compiled without changing its meaning."""
    def __init__(self, message, required=None):
        super().__init__(message)
        self.required = required


class QueryCompiler:
    def __init__(self, metadata, database):
        self.metadata = metadata
        self.departments = {f"D{i+1}": d for i, d in enumerate(metadata["departments"])}
        universities = sorted({(d["universityLabel"], d["university"]) for d in self.departments.values()})
        self.universities = {f"U{i+1}": {"label": label, "uri": uri} for i, (label, uri) in enumerate(universities)}
        self.catalogue = {q["id"]: q for q in entries()}
        self.vocabulary = metadata["vocabulary"]
        self.staff_groups = {g["id"]: g for g in metadata["staffGroups"]}

    def context(self):
        university_ids = {v["uri"]: k for k, v in self.universities.items()}
        return {
            "universities": {k: v["label"] for k, v in self.universities.items()},
            "departments": {k: f"{v['label']}; {university_ids[v['university']]}; city={v['cityLabel']}; region={v['regionLabel']}" for k, v in self.departments.items()},
            "datasets": {k: {"label": v["label"], "metrics": v["metrics"], **self.vocabulary[k]} for k, v in DATASETS.items()},
            "staffGroups": {k: {"label": g["label"], "members": g["members"]} for k, g in self.staff_groups.items()},
            "catalogue": [{k: q[k] for k in ("id", "title", "filters")} for q in self.catalogue.values()],
        }

    def selected_departments(self, plan):
        if set(plan.departments) - self.departments.keys() or set(plan.universities) - self.universities.keys():
            raise Clarification("Choose an institution included in the UniStats dataset.")
        for field in ("city", "region"):
            value = getattr(plan, field)
            if value and value not in {d[field + "Label"] for d in self.departments.values()}:
                raise Clarification(f"That {field} is not in the dataset. Please use a listed location.")
        universities = {self.universities[u]["uri"] for u in plan.universities}
        selected = [d for key, d in self.departments.items()
                    if (not plan.departments or key in plan.departments)
                    and (not universities or d["university"] in universities)
                    and (not plan.city or d["cityLabel"] == plan.city)
                    and (not plan.region or d["regionLabel"] == plan.region)]
        if not selected:
            raise Clarification("Those institution and location filters do not overlap. Please adjust them.")
        return selected

    @staticmethod
    def check_read_only_intent(question: Question):
        # These are requests for unsupported operations, not executable input.
        # The compiler's SELECT-only construction is the actual write boundary.
        if re.search(r"\b(?:DROP\s+(?:ALL|GRAPH|TABLE)|DELETE\s+(?:WHERE|ALL|FROM)|INSERT\s+(?:DATA|INTO)|CLEAR\s+(?:ALL|GRAPH)|LOAD\s*<|SERVICE\s*<)", question.question, re.IGNORECASE):
            raise Clarification("UniStats answers read-only statistics questions. Database changes and external SPARQL services are not supported; no query was executed.")

    @staticmethod
    def validate_years(years, available):
        if any(not re.fullmatch(r"AY20\d{2}_20\d{2}", y) or y not in available for y in years):
            raise Clarification("No covered academic year matches that request. Available years: " + ", ".join(y[2:].replace("_", "–") for y in available) + ".")
        return sorted(set(years))

    def validate_question(self, question: Question, plan: QueryPlan):
        """Cross-check unambiguous literals that a small model must not drop."""
        if plan.mode == "clarify":
            return
        text = question.question.casefold()
        required, issues = {}, []
        explicit = []
        for start, end in re.findall(r"\b(20\d{2})\s*[-–/]\s*(20\d{2}|\d{2})\b", text):
            end = int(end) if len(end) == 4 else 2000 + int(end)
            if end == int(start) + 1:
                explicit.append(f"AY{start}_{end}")
        if explicit and not set(explicit).issubset(plan.years):
            required["years"] = explicit
            issues.append("Preserve the explicitly requested academic year.")
        named = {key for key, university in self.universities.items() if university["label"].casefold() in text}
        if named:
            expected = {d["uri"] for d in self.departments.values() if d["university"] in {self.universities[k]["uri"] for k in named}}
            actual = {d["uri"] for d in self.selected_departments(plan)}
            if not actual.issubset(expected):
                required["universities"] = sorted(named)
                issues.append("Preserve the named university's scope.")
        if "how many" in text and ("female" in text or "women" in text) and plan.metric == "femaleShare":
            required.update(metric="total", gender="Female")
            issues.append("Use a female count, not a percentage.")
        if "compare" in text and "msc" in text and "phd" in text and (plan.group_by != "level" or plan.level or plan.category):
            required.update(dataset="postgraduates", group_by="level", level=None, category=None)
            issues.append("Compare both MSc and PhD study levels.")
        if plan.mode == "statistics" and any(term in text for term in ("over time", "trend", "each year", "year by year", "each academic year")) and plan.group_by != "year":
            required["group_by"] = "year"
            issues.append("Show a separate value for each academic year.")
        if plan.mode == "statistics" and plan.metric != "total" and plan.gender and any(term in text for term in ("share", "percentage", "percent", "proportion")):
            required["gender"] = None
            issues.append("Keep both genders in the percentage denominator.")
        if required:
            raise Clarification("The model could not preserve the question's meaning. " + " ".join(issues) + " Please rephrase; no query was executed.", required)

    def compile(self, plan: QueryPlan):
        if plan.mode == "clarify":
            raise Clarification(plan.message or "Please specify a population, year, and institution or comparison.")
        selected = self.selected_departments(plan)
        if plan.mode == "catalogue":
            return self.compile_catalogue(plan, selected)
        birth_mode = plan.birth_year is not None or plan.group_by == "birth_year"
        config = population_config(plan.dataset, "birth" if birth_mode else None)
        vocab = self.vocabulary[plan.dataset]
        categories = vocab["birthCategories"] if birth_mode else vocab["categories"]
        if plan.query_id or plan.metric not in config["metrics"]:
            raise Clarification("That metric is not available for this population.")
        if birth_mode and (plan.dataset in {"staff", "external"} or not vocab["birthYears"]):
            raise Clarification("This population has no birth-year observations.")
        if plan.birth_year is not None and plan.birth_year not in vocab["birthYears"]:
            raise Clarification("That exact birth year/group is unavailable. Choose one of the recorded birth-year values.")
        if birth_mode and plan.dataset == "graduates" and (plan.category or plan.group_by == "category"):
            raise Clarification("Graduate birth year and degree grade are separate aggregate tables and cannot be combined.")
        if plan.staff_group or plan.group_by == "staff_group":
            if plan.dataset not in {"staff", "external"}:
                raise Clarification("Broad staff categories apply only to teaching staff.")
            if plan.staff_group and plan.staff_group not in self.staff_groups:
                raise Clarification("Choose a broad staff category from the available vocabulary.")
        if plan.level and plan.dataset != "postgraduates":
            raise Clarification("MSc and PhD filters apply to postgraduate students.")
        if plan.category and plan.category not in categories:
            raise Clarification("Choose a category available for this population: " + ", ".join(sorted(categories)) + ".")
        if plan.metric != "total" and (plan.gender or plan.category or plan.group_by in {"gender", "category"}):
            raise Clarification("A percentage needs the full population as its denominator. Ask for the percentage without a gender/category filter, or ask for a count.")
        if plan.group_by == "level" and plan.dataset != "postgraduates":
            raise Clarification("Comparing MSc and PhD levels requires the postgraduate population.")
        if plan.top and plan.group_by in {"total", "year"}:
            raise Clarification("Top rankings require a category such as department or university.")
        available_years = vocab["birthAcademicYears"] if birth_mode else vocab["years"]
        years = self.validate_years(plan.years, available_years)
        if not available_years:
            raise Clarification("This population has no recorded academic years in the selected source.")
        assumptions = []
        if not years:
            years = available_years if plan.group_by == "year" else available_years[-1:]
            assumptions.append("All covered academic years." if plan.group_by == "year" else "No year specified: using the latest covered academic year, " + years[0][2:].replace("_", "–") + ".")
        if len(years) > 1 and plan.group_by != "year":
            assumptions.append("Counts across years are summed observations, not distinct people across years.")
        staff = plan.dataset in {"staff", "external"}
        institution = "department ?department" if staff else "program ?program"
        body = f"?o qb:dataSet <{BASE}dataset/{config['dataset']}> ; dim:academicYear ?academicYear ; dim:{institution} ; dim:gender ?gender ; dim:count ?n .\n"
        if not staff:
            body += "?department uni:hasUniversityProgram ?program . ?program uni:hasUniversityProgramType ?programType .\n"
            levels = [plan.level] if plan.level else (["MSc", "PhD"] if plan.dataset == "postgraduates" else ["BSc"])
            body += "VALUES ?programType { " + " ".join("res:" + level for level in levels) + " }\n"
        body += f"?o dim:{config['dimension']} ?category .\n" if config["dimension"] else "BIND(?programType AS ?category)\n"
        if staff:
            body += staff_detail_clause(plan.dataset) + "\n"
        if birth_mode:
            body += "?o dim:birthYear ?birthValue . BIND(STR(?birthValue) AS ?birth_year)\n"
            if plan.birth_year is not None:
                body += "FILTER(?birth_year = " + json.dumps(plan.birth_year, ensure_ascii=False) + ")\n"
        if plan.staff_group or plan.group_by == "staff_group":
            mappings = []
            for category, uri in vocab["categories"].items():
                groups = [g for g in self.staff_groups.values() if category in g["members"]]
                if plan.staff_group:
                    groups = [g for g in groups if g["id"] == plan.staff_group]
                elif len(groups) > 1:
                    raise Clarification("Some staff categories have multiple broader groups; select one group explicitly.")
                for g in groups:
                    mappings.append(f"(<{uri}> <{g['uri']}>)")
            body += "VALUES (?category ?staff_group) { " + " ".join(mappings) + " }\n"
        body += "VALUES ?academicYear { " + " ".join("res:" + y for y in years) + " }\n"
        if plan.gender:
            body += f"VALUES ?gender {{ res:{plan.gender} }}\n"
        category = "StudyYear1" if plan.dataset == "entry" else plan.category
        if category:
            body += f"VALUES ?category {{ <{categories[category]}> }}\n"
        # Include only the location dimension used by this query. This avoids
        # sending four redundant IRIs per department on every remote request.
        keys = ["uri"] + ([plan.group_by] if plan.group_by in {"university", "city", "region"} else [])
        variables = ["department"] + keys[1:]
        body += "VALUES (" + " ".join("?" + v for v in variables) + ") {\n" + "\n".join(
            "(" + " ".join(f"<{d[k]}>" for k in keys) + ")" for d in selected) + "\n}\n"
        grouped = plan.group_by != "total"
        if grouped:
            variable = {"year": "academicYear", "level": "programType"}.get(plan.group_by, plan.group_by)
            body += f"BIND(?{variable} AS ?group)\n"
        expression = "SUM(?n)"
        if plan.metric != "total":
            condition = {"femaleShare": "?gender = res:Female", "beyondShare": "?category = res:BeyondNormalYears", "excellentShare": "?category = res:Excellent"}[plan.metric]
            # A zero denominator leaves ?value unbound; it is never a measured 0%.
            expression = f"(100.0 * SUM(IF({condition}, ?n, 0)) / SUM(?n))"
        projection = ("?group " if grouped else "") + "?value ?observations"
        aggregate = ("?group " if grouped else "") + f"({expression} AS ?value) (COUNT(?o) AS ?observations)"
        order = "ORDER BY ?group" if plan.group_by == "year" else "ORDER BY DESC(?value)" + (" ?group" if grouped else "")
        core = f"SELECT {aggregate} WHERE {{\n{body}}}\n" + ("GROUP BY ?group\n" if grouped else "") + order
        if plan.top:
            core += f"\nLIMIT {plan.top}"
        query = PREFIXES + f"SELECT {projection}\n{FROM}\nWHERE {{ {{\n{core}\n}} }}\n{order}"
        scope = ", ".join(sorted({d['universityLabel'] for d in selected})) if len(selected) != len(self.departments) else "all included departments"
        if plan.departments:
            scope = ", ".join(self.departments[d]["label"] + " — " + self.departments[d]["universityLabel"] for d in plan.departments)
        filters = ", ".join(x for x in (plan.gender, plan.level, plan.category, plan.staff_group, plan.birth_year, plan.city, plan.region) if x)
        interpretation = f"{METRICS[plan.metric]} of {config['label'].lower()} for {scope}"
        if filters:
            interpretation += f" ({filters})"
        if grouped:
            interpretation += f", by {plan.group_by}"
        if plan.top:
            interpretation += f", top {plan.top}"
        interpretation += ": " + ", ".join(y[2:].replace("_", "–") for y in years) + "."
        return {"kind": "statistics", "query": query, "interpretation": interpretation,
                "assumptions": assumptions, "note": config["note"] + f" Coverage includes {len(self.departments)} departments in the selected database metadata.",
                "unit": "%" if plan.metric != "total" else config["unit"], "years": years, "plan": plan.model_dump()}

    def compile_catalogue(self, plan, selected):
        if plan.query_id not in self.catalogue:
            raise Clarification("Choose an available thesis indicator.")
        if plan.metric != "total" or plan.group_by != "total" or plan.top or plan.category or plan.staff_group or plan.birth_year is not None:
            raise Clarification("This thesis indicator has a fixed grouping. Ask for the indicator with its supported filters.")
        if len(plan.years) > 1:
            raise Clarification("For this thesis indicator, choose one academic year or leave the year open.")
        available = sorted({y for v in self.vocabulary.values() for y in v["years"]})
        years = self.validate_years(plan.years, available)
        filters = {}
        if years:
            filters["academicYear"] = years[0]
        if plan.gender:
            filters["gender"] = plan.gender
        if plan.level:
            filters["programType"] = plan.level
        scoped = bool(plan.departments or plan.universities or plan.city or plan.region)
        if scoped:
            filters["department"] = selected[0]["uri"]
        try:
            query = filtered_query(plan.query_id, filters, {d["uri"] for d in self.departments.values()})
        except ValueError as exc:
            raise Clarification(str(exc)) from exc
        if scoped:
            clause = "VALUES ?department { " + " ".join(f"<{d['uri']}>" for d in selected) + " }"
            query = re.sub(r"VALUES \?department \{[^}]+\}", lambda _: clause, query, count=1)
        title = self.catalogue[plan.query_id]["title"]
        filter_text = ", ".join(x for x in ([y[2:].replace("_", "–") for y in years] + [plan.gender, plan.level, plan.city, plan.region]) if x)
        if scoped:
            filter_text += "; " + ", ".join(sorted({d["universityLabel"] for d in selected}))
        return {"kind": "catalogue", "query": query, "interpretation": title + (": " + filter_text if filter_text else "") + ".",
                "assumptions": ["Uses the corrected thesis query's fixed definition and grouping."],
                "note": description(plan.query_id), "unit": None, "years": years, "plan": plan.model_dump()}

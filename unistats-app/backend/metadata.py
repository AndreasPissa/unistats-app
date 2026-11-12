"""Discover institutions, coverage and SKOS vocabulary from the selected RDF source."""
import asyncio
import hashlib
import json
import math
import time
from urllib.parse import urlsplit

from .analytics import DATASETS, METRICS, short
from .catalogue import QUERIES
from .config import PREFIXES, FROM, BASE, CACHE_SECONDS, DEFAULT_SOURCE
from .database import DatabaseUnavailable
from .institutions import load_institutions
from .locations import load_locations

COVERAGE_QUERY = PREFIXES + f"""
SELECT DISTINCT ?dataset ?academicYear ?category ?birthYear ?programType
{FROM} WHERE {{
  ?o qb:dataSet ?dataset ; dim:academicYear ?academicYear ; dim:count ?count .
  FILTER(STRSTARTS(STR(?dataset), "{BASE}dataset/"))
  OPTIONAL {{ ?o dim:program ?p . ?p uni:hasUniversityProgramType ?programType }}
  OPTIONAL {{ ?o dim:staffCategory|dim:studyYear|dim:degreeGrade ?category }}
  OPTIONAL {{ ?o dim:birthYear ?birthYear }}
}} ORDER BY ?dataset ?academicYear ?category ?birthYear ?programType
"""
CONCEPT_QUERY = PREFIXES + f"""
SELECT DISTINCT ?concept ?parent ?label ?labelEl {FROM} WHERE {{
  {{ ?concept a skos:Concept }} UNION {{ ?concept skos:broader ?any }}
  UNION {{ ?any skos:broader ?concept }}
  OPTIONAL {{ ?concept skos:broader ?parent }}
  OPTIONAL {{ ?concept rdfs:label|skos:prefLabel ?label FILTER(lang(?label) = "en") }}
  OPTIONAL {{ ?concept rdfs:label|skos:prefLabel ?labelEl FILTER(lang(?labelEl) = "el") }}
}} ORDER BY ?concept ?parent ?label ?labelEl
"""


def ancestors(uri, concepts):
    seen, todo = set(), list(concepts.get(uri, {}).get("parents", []))
    while todo:
        parent = todo.pop()
        if parent in seen or parent == uri:
            continue
        seen.add(parent)
        todo.extend(concepts.get(parent, {}).get("parents", []))
    return seen


def build_metadata(records, coverage, concept_rows, source):
    institutions = load_institutions()  # Website redirect overrides, not an institution allowlist.
    locations = load_locations()
    departments = {}
    for r in records:
        uri = r["department"]
        if uri not in departments:
            location = None
            try:
                lat, lon = float(r["lat"]), float(r["long"])
                if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    location = {"latitude": lat, "longitude": lon, "source": "RDF coordinates"}
            except (KeyError, ValueError, TypeError):
                pass
            departments[uri] = {
                "uri": uri, "label": r.get("deptLabelEn", r.get("deptLabelEl", uri)),
                "labelEl": r.get("deptLabelEl", ""), "university": r.get("university", ""),
                "universityLabel": r.get("uniLabelEn", r.get("uniLabelEl", "Unknown university")),
                "city": r.get("city", ""), "cityLabel": short(r.get("city", "")).replace("_", " "),
                "region": r.get("region", ""), "regionLabel": short(r.get("region", "")).replace("_", " "),
                "school": r.get("school", ""), "schoolLabel": r.get("schoolLabelEn", r.get("schoolLabelEl", "")),
                "duration": None, "programs": [],
                "location": (locations["locations"].get(uri) or location) if source["mode"] == "snapshot" else (location or locations["locations"].get(uri)),
            }
        d = departments[uri]
        if r.get("program"):
            program = {"uri": r["program"], "type": short(r.get("programType", ""))}
            if program not in d["programs"]:
                d["programs"].append(program)
        if r.get("durationYears"):
            d["duration"] = int(r["durationYears"])
        for identifier, label, kind in [(uri, d["label"], "department"), (d["school"], d["schoolLabel"], "school"),
                                        (d["university"], d["universityLabel"], "university")]:
            if not identifier:
                continue
            parsed = urlsplit(identifier)
            url = identifier if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
            override = institutions.get(identifier, {})
            institutions[identifier] = {"kind": kind, "label": label, "url": override.get("url", url)}
    concepts = {}
    for r in concept_rows:
        c = concepts.setdefault(r["concept"], {"id": short(r["concept"]), "uri": r["concept"], "label": short(r["concept"]), "parents": []})
        if r.get("label"):
            c["label"] = r["label"]
        if r.get("labelEl"):
            c["labelEl"] = r["labelEl"]
        if r.get("parent") and r["parent"] not in c["parents"]:
            c["parents"].append(r["parent"])
    staff_uris = {r["category"] for r in coverage if r.get("category") and short(r["dataset"]) in {DATASETS[k]["dataset"] for k in ("staff", "external")}}
    staff_nodes = staff_uris | {a for c in staff_uris for a in ancestors(c, concepts)}
    parents = {p for c in concepts.values() for p in c["parents"]}
    groups = []
    for uri in sorted(staff_nodes):
        c = concepts.get(uri, {"id": short(uri), "uri": uri, "label": short(uri), "parents": []})
        # A broad group is directly below a root; unclassified observed values stay visible.
        if (any(not concepts.get(p, {}).get("parents") for p in c["parents"])
                or (uri in staff_uris and not c["parents"] and uri not in parents)):
            members = sorted(short(v) for v in staff_uris if v == uri or uri in ancestors(v, concepts))
            groups.append({**c, "members": members})
    vocab = {}
    for dataset, config in DATASETS.items():
        selected = [r for r in coverage if short(r["dataset"]) == config["dataset"]
                    and (dataset != "postgraduates" or short(r.get("programType", "")) in {"MSc", "PhD"})
                    and (dataset != "entry" or short(r.get("category", "")) == "StudyYear1")]
        birth_cube = {"students": "studentsByBirthYearStudyYearDataset", "graduates": "graduatesByBirthYearDataset"}.get(dataset, config["dataset"])
        birth = [r for r in coverage if short(r["dataset"]) == birth_cube and r.get("birthYear")
                 and (dataset not in {"students", "entry", "graduates"} or short(r.get("programType", "")) == "BSc")
                 and (dataset != "postgraduates" or short(r.get("programType", "")) in {"MSc", "PhD"})
                 and (dataset != "entry" or short(r.get("category", "")) == "StudyYear1")]
        vocab[dataset] = {"years": sorted({short(r["academicYear"]) for r in selected}),
                          "categories": {short(r.get("category") or r.get("programType", "")): r.get("category") or r.get("programType", "") for r in selected},
                          "birthCategories": {short(r.get("category") or r.get("programType", "")): r.get("category") or r.get("programType", "") for r in birth},
                          "birthYears": sorted({r["birthYear"] for r in birth}),
                          "birthAcademicYears": sorted({short(r["academicYear"]) for r in birth})}
    result = {"departments": sorted(departments.values(), key=lambda d: (d["universityLabel"], d["label"])),
              "institutions": institutions, "concepts": concepts, "staffGroups": groups, "vocabulary": vocab,
              "availableYears": sorted({short(r["academicYear"]) for r in coverage}),
              "locationSource": {k: locations[k] for k in ("source", "sha256", "prepared")},
              "datasets": [{"id": k, **v} for k, v in DATASETS.items()], "metrics": METRICS,
              "defaultSource": DEFAULT_SOURCE, "source": source,
              "metadataSource": "Live database" if source["mode"] == "live" else "Local RDF snapshot"}
    result["revision"] = hashlib.sha256(json.dumps({k: v for k, v in result.items() if k != "source"}, sort_keys=True).encode()).hexdigest()[:16]
    return result


class MetadataStore:
    def __init__(self, database):
        self.database = database
        self.cache = {}
        self.lock = asyncio.Lock()

    async def get(self, source="auto", *, fresh=False):
        async with self.lock:
            cached = self.cache.get(source)
            if not fresh and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
                return cached[1]
            queries = [QUERIES["01_school_metadata"], COVERAGE_QUERY, CONCEPT_QUERY]
            results = await asyncio.gather(*(self.database.query(q, source, fresh=fresh) for q in queries))
            # Never label a mixed bundle as live. Auto falls back as a complete bundle.
            modes = {r["source"]["mode"] for r in results}
            if len(modes) > 1:
                results = await asyncio.gather(*(self.database.query(q, "snapshot", fresh=fresh) for q in queries))
                results[0]["source"] = {**results[0]["source"], "reason": "Live metadata was incomplete; using the RDF snapshot."}
            if not results[0]["rows"]:
                raise DatabaseUnavailable("No department metadata was returned by the selected source.")
            result = build_metadata(*(r["rows"] for r in results), results[0]["source"])
            self.cache[source] = (time.monotonic(), result)
            return result

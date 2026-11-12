"""Backend configuration; process environment takes precedence over .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False, interpolate=False, encoding="utf-8-sig")
ENDPOINT = os.getenv("UNISTATS_ENDPOINT", "http://93.115.20.167:8890/sparql")
DEFAULT_SOURCE = os.getenv("UNISTATS_SOURCE", "auto")
SNAPSHOT_DATE = "2026-10-08"
CACHE_SECONDS = 300
MAX_ROWS = 25000
BASE = "http://www.ics.forth.gr/uniStats/"
GRAPHS = ["http://www.ics.forth.gr/isl/uniStats", "http://www.ics.forth.gr/isl/uniStats_departments", "http://www.ics.forth.gr/isl/uniStats/personnel"]
PREFIXES = f"""PREFIX qb: <http://purl.org/linked-data/cube#>
PREFIX dim: <{BASE}dimension/>
PREFIX res: <{BASE}resource/>
PREFIX uni: <{BASE}ontology/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""
FROM = "\n".join(f"FROM <{g}>" for g in GRAPHS)

"""UniStats HTTP API and static HTML/CSS/JS application."""
import asyncio
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

from .config import ROOT, DEFAULT_SOURCE
from .database import Database, DatabaseUnavailable
from .catalogue import QUERIES, entries, filtered_query
from .analytics import DATASETS, METRICS, explorer_query, explore
from .metadata import MetadataStore
from . import ai

Source = Literal["auto", "live", "snapshot"]
db: Database | None = None
metadata = {}
assistant: ai.Assistant | None = None


metadata_store: MetadataStore | None = None


@asynccontextmanager
async def lifespan(app):
    global db, metadata, assistant, metadata_store
    db = await asyncio.to_thread(Database)
    metadata_store = MetadataStore(db)
    metadata = await metadata_store.get("snapshot")
    assistant = await asyncio.to_thread(ai.Assistant, metadata, db, metadata_store)
    try:
        yield
    finally:
        await assistant.close()
        await db.close()


app = FastAPI(title="UniStats API", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
              description="Read-only university statistics. Approved SPARQL templates and a real local RDF fallback.")
requests_by_ip = defaultdict(deque)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        now = time.monotonic()
        if len(requests_by_ip) > 2048:
            for key in list(requests_by_ip):
                if not requests_by_ip[key] or now - requests_by_ip[key][-1] > 60: del requests_by_ip[key]
        bucket = requests_by_ip[request.client.host if request.client else "unknown"]
        while bucket and now - bucket[0] > 60: bucket.popleft()
        if len(bucket) >= 120:
            return JSONResponse({"detail": "Too many requests. Please wait a moment."}, status_code=429, headers={"Retry-After": "60"})
        bucket.append(now)
        if request.method == "POST" and "content-length" not in request.headers:
            return JSONResponse({"detail": "Content-Length is required."}, status_code=411)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
        if length < 0:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
        if length > 16384:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' https://vector.openstreetmap.org; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response


@app.exception_handler(DatabaseUnavailable)
async def database_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/api/health")
async def health():
    return {"status": "ok", "database": db.remote_state, "snapshotReady": True, "aiEnabled": assistant.enabled}


@app.get("/api/metadata")
async def get_metadata(source: Source = DEFAULT_SOURCE, refresh: bool = False):
    return await metadata_store.get(source, fresh=refresh)


@app.get("/api/explore")
async def get_explore(dataset: str = "students", year: str | None = None, university: str | None = None,
                      department: str | None = None, region: str | None = None, city: str | None = None,
                      gender: Literal["Male", "Female"] | None = None,
                      level: Literal["MSc", "PhD"] | None = None, category: str | None = None,
                      staff_group: str | None = None, birth_year: str | None = None,
                      category_detail: Literal["broad", "specific"] = "broad", refresh: bool = False,
                      metric: str = "total", group_by: Literal["department", "university", "region", "city"] = "department",
                      top: int = Query(10, ge=3, le=500), source: Source = DEFAULT_SOURCE):
    if dataset not in DATASETS or metric not in DATASETS[dataset]["metrics"]:
        raise HTTPException(422, "Choose a valid dataset and metric.")
    query = explorer_query(dataset, birth_year)
    result = await db.query(query, source, fresh=refresh)
    selected_metadata = await metadata_store.get(result["source"]["mode"], fresh=refresh)
    known = selected_metadata["departments"]
    for value, key in [(university, "university"), (department, "uri"), (region, "region"), (city, "city")]:
        if value and value not in {d[key] for d in known}: raise HTTPException(422, "Unknown institution or location.")
    try:
        response = explore(result["rows"], selected_metadata, dataset=dataset, year=year, university=university,
                           department=department, region=region, city=city, gender=gender, level=level, category=category,
                           metric=metric, group_by=group_by, top=top, staff_group=staff_group, birth_year=birth_year, category_detail=category_detail)
    except ValueError as exc: raise HTTPException(422, str(exc))
    return {**response, "source": result["source"], "query": query, "metadata": selected_metadata,
            "filtering": "SPARQL retrieves the selected cube; the API applies the displayed institution, year and dimension filters to its grouped counts."}


@app.get("/api/catalogue")
async def catalogue(): return {"queries": entries()}


class CatalogueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source = DEFAULT_SOURCE
    filters: dict[str, str] = Field(default_factory=dict, max_length=4)


@app.post("/api/catalogue/{query_id}/run")
async def run_catalogue(query_id: str, request: CatalogueRequest):
    try:
        current_metadata = await metadata_store.get(request.source)
        query = filtered_query(query_id, request.filters, {d["uri"] for d in current_metadata["departments"]})
    except ValueError as exc: raise HTTPException(422, str(exc))
    result = await db.query(query, request.source)
    return {**result, "queryId": query_id, "query": query, "totalRows": len(result["rows"])}


@app.get("/api/ai/status")
async def ai_status(): return await assistant.status()


@app.post("/api/ai/query")
async def ai_query(question: ai.Question):
    try:
        return await assistant.ask(question)
    except ai.AIBusy as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "10"}) from exc
    except ai.AIUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


app.mount("/assets", StaticFiles(directory=ROOT / "public"), name="assets")


@app.get("/", include_in_schema=False)
async def index(): return FileResponse(ROOT / "public/index.html")

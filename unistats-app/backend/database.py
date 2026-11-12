"""Read-only SPARQL transport, bounded cache, circuit breaker and real RDF fallback."""
import asyncio
import hashlib
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from pyoxigraph import Store, NamedNode, RdfFormat
from .config import ROOT, ENDPOINT, GRAPHS, SNAPSHOT_DATE, CACHE_SECONDS, MAX_ROWS


class DatabaseUnavailable(Exception):
    pass


class Database:
    def __init__(self):
        self.store = Store()  # In-memory; the supplied Turtle files remain read-only.
        for file in sorted((ROOT / "data/rdf").glob("*.ttl")):
            graph = GRAPHS[1] if file.name == "sxoles_complete.ttl" else (
                GRAPHS[2] if file.name in {"21_all.ttl", "22_all.ttl", "concepts_21.ttl"} else GRAPHS[0])
            self.store.load(path=file, format=RdfFormat.TURTLE, to_graph=NamedNode(graph))
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=2), follow_redirects=False)
        self.cache = OrderedDict()
        self.remote_retry_at = 0.0
        self.remote_state = "not_checked"
        self.limit = asyncio.Semaphore(4)

    async def close(self):
        await self.client.aclose()

    def local(self, query):
        result = self.store.query(query)
        variables = [v.value for v in result.variables]
        rows = []
        for row in result:
            if len(rows) >= MAX_ROWS:
                raise DatabaseUnavailable("The result is too large. Narrow your filters.")
            rows.append({k: row[k].value for k in variables if row[k] is not None})
        return {"columns": variables, "rows": rows}

    async def _remote_page(self, query):
        # Only server-owned SELECT templates reach this method. The endpoint is not user input.
        async with self.client.stream("GET", ENDPOINT, params={
            "query": query, "format": "application/sparql-results+json", "timeout": "10000"
        }, headers={"Accept": "application/sparql-results+json"}) as response:
            response.raise_for_status()
            if response.headers.get("x-sql-state", "00000") != "00000":
                raise DatabaseUnavailable("The database did not return a complete result.")
            server_cap = int(response.headers.get("x-sparql-maxrows", "5000"))
            if server_cap < 1:
                raise DatabaseUnavailable("The database returned an invalid row limit.")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 24 * 1024 * 1024:
                    raise DatabaseUnavailable("The database result exceeds the response limit.")
        import json
        data = json.loads(payload)
        bindings = data["results"]["bindings"]
        if len(bindings) > MAX_ROWS:
            raise DatabaseUnavailable("The result is too large. Narrow your filters.")
        return {"columns": data["head"]["vars"], "rows": [
            {k: v["value"] for k, v in row.items()} for row in bindings], "serverCap": server_cap}

    async def remote(self, query):
        # Virtuoso caps a response at 10,000 rows without an error. Page the
        # ordered, server-owned templates so large master queries stay complete.
        rows, columns, page_size = [], None, 5000
        while True:
            page_query = query
            if rows:
                # Virtuoso's sorted-TOP limit also caps LIMIT + OFFSET. Its
                # documented subquery cursor pattern avoids that separate cap.
                start = re.search(r"(?im)^SELECT\b", query)
                if not start:
                    raise DatabaseUnavailable("Only SELECT queries can be paginated.")
                projection = " ".join("?" + name for name in columns)
                page_query = query[:start.start()] + f"SELECT {projection} WHERE {{ {{\n" + query[start.start():] + "\n} }"
            page = await self._remote_page(page_query + f"\nLIMIT {page_size} OFFSET {len(rows)}")
            if columns is not None and columns != page["columns"]:
                raise DatabaseUnavailable("The database result changed during retrieval.")
            columns = page["columns"]
            rows.extend(page["rows"])
            if len(rows) > MAX_ROWS:
                raise DatabaseUnavailable("The result is too large. Narrow your filters.")
            page_size = min(page_size, page["serverCap"])
            if len(page["rows"]) < page_size:
                return {"columns": columns, "rows": rows}

    async def query(self, query, mode="auto", *, fresh=False):
        key = (mode, hashlib.sha256(query.encode()).hexdigest())
        cached = self.cache.get(key)
        if not fresh and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            self.cache.move_to_end(key)
            return {**cached[1], "source": {**cached[1]["source"], "cached": True}}
        async with self.limit:
            source, reason = "snapshot", None
            if mode != "snapshot" and (mode == "live" or time.monotonic() >= self.remote_retry_at):
                try:
                    async with asyncio.timeout(35):
                        result = await self.remote(query)
                    source = "live"
                    self.remote_state = "connected"
                    self.remote_retry_at = 0
                except (httpx.HTTPError, TimeoutError, ValueError, KeyError, DatabaseUnavailable):
                    self.remote_state = "unavailable"
                    self.remote_retry_at = time.monotonic() + 60
                    if mode == "live":
                        raise DatabaseUnavailable("The live database is unavailable. Try Auto or Local snapshot.")
                    reason = "The live database is unavailable; showing the supplied RDF snapshot."
            elif mode == "auto":
                reason = "The live database is unavailable; showing the supplied RDF snapshot."
            if source == "snapshot":
                result = await asyncio.to_thread(self.local, query)
            result["source"] = {
                "mode": source, "requested": mode, "cached": False,
                "snapshotDate": SNAPSHOT_DATE if source == "snapshot" else None,
                "retrievedAt": datetime.now(timezone.utc).isoformat(), "reason": reason,
            }
            self.cache[key] = (time.monotonic(), result)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return result

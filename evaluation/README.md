# UniStats SPARQL evaluation

Adapted from the supervisor's examples:

- [Knowledge graph statistics](https://colab.research.google.com/drive/1k_PScEOvqDdrojLxMCgwOtfTR6nLpnW4)
- [Five-run query benchmark](https://colab.research.google.com/drive/1-f6CQkm--rboQ8849_yiGhtXt-A8Dlo1)

The adaptation replaces the basketball queries with 20 unchanged UniStats catalogue queries. `query_manifest.json` maps Q1–Q20 to their catalogue identifiers and SHA-256 hashes. `queries/` contains their complete SPARQL text. `statistics_queries/` contains the graph-size queries, using DISTINCT triples across the three named graphs.

```sh
python -m pip install -r evaluation/requirements.txt
python evaluation/run_evaluation.py --stats --benchmark --output evaluation/results-new
```

Run from the repository root. Use Python 3.11 or newer. `--endpoint`, `--runs` (default 5) and `--timeout` (default 300 seconds) are configurable. The endpoint must be reachable. These are read-only SELECTs. They run directly against the endpoint without API keys, application cache or snapshot fallback.

Timing starts immediately before the HTTP request and ends after parsing the JSON response. It includes network and serialization overhead; it is **not** an isolated server CPU measurement or an end-to-end UI measurement. Runs are sequential, five consecutive runs per query, without an explicit warmup. Server caches are not cleared. The statistics phase precedes the benchmark in the recorded run.

The summary reports mean, median, sample standard deviation, minimum, maximum and result rows. All five runs must succeed and return the same result hash before a query is marked complete. Failures remain in the raw evidence; there are no silent retries or invented zero-row results. Endpoint partial-result headers and response row limits are checked.

`results/` contains raw times, CSV/JSON summaries, database statistics and environment details. The runner also saves full JSON bindings when executed; these generated responses are omitted from the public repository to keep it compact. Use a new output folder when rerunning to preserve the published measurements. Row counts refer to returned solution rows, not people or distinct RDF triples.

The original reference notebooks are retained privately with the thesis working material; this repository includes the adapted runner and links to the originals.

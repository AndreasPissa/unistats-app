# UniStats

**Explore and compare statistics from university Computer Science departments in Greece.**

UniStats is a thesis project that connects statistical Excel workbooks to an interactive web application. Python scripts transform the source data into RDF, SPARQL queries retrieve and compare the statistics, and a web interface presents the results through tables, charts and a map.

Developed by **Andreas Pissaridis (csd5291)** at the **Department of Computer Science, University of Crete**, under the supervision of **Michalis Mountantonakis**.

Thesis: *Modeling, Visualization and Comparison of Data from University Computer Science Departments*.

## Features

**[Open the live application](http://93.115.20.167:8080/?year=AY2023_2024)** — this link opens UniStats with academic year 2023–2024 selected.

- Explore student populations, graduates, postgraduate studies and teaching staff.
- Compare departments and academic years using interactive charts and tables.
- Filter supported datasets by department, gender, birth year and staff category.
- Find university departments on an interactive, clickable map.
- Browse and execute a catalogue of 41 SPARQL queries.
- Switch between light and dark themes.
- Optionally ask questions in English through an AI-assisted SPARQL interface. A provider key is required only for this feature.

The application can query a live Virtuoso database or use the included local RDF snapshot. The data describe aggregate statistics, not individual student records.

## How it works

```text
Excel workbooks -> Python ETL -> RDF / Data Cube -> SPARQL -> FastAPI -> Web interface
```

The backend uses Python and FastAPI. The frontend uses HTML, CSS and JavaScript, with Chart.js for charts and Leaflet for the map. RDF models the statistics and their dimensions so that the same data can be queried across departments and years.

## Quick start

Install **Python 3.11 or newer**. From the repository root, on Windows:

```powershell
cd unistats-app
.\start.cmd
```

On Linux or macOS:

```sh
cd unistats-app
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Node.js is not required to run the application; frontend libraries are included. Map tiles and live database queries require network access.

For custom settings, copy `unistats-app/.env.example` to `unistats-app/.env` and edit it locally. See [AI setup](unistats-app/AI_INTEGRATION.md) to configure an optional provider. Never commit your `.env` or API keys.

## Repository structure

| Location | Contents |
|---|---|
| `unistats-app/` | Application, API, query catalogue, local RDF data and tests |
| `etl_*.py` | Excel-to-RDF transformation scripts |
| `data/` | Source Excel workbooks |
| `cs_schools.xlsx` | Department information and geographic coordinates |
| `requirements-etl.txt` | ETL dependencies |
| `rdf_output/` | Additional RDF datasets outside the application's main views |
| `evaluation/` | Database statistics, 20 benchmark queries, measurement code and recorded timings |

## RDF transformations

Install the ETL dependencies in a separate Python environment and run the scripts from the repository root:

```sh
pip install -r requirements-etl.txt
python etl_04.py cs_schools.xlsx data/04/*.xls
```

Each script's header describes its inputs and options. If your shell does not expand wildcards, pass the workbook paths explicitly. Generated RDF does not automatically replace the application's snapshot or update the live database.

## Evaluation

The evaluation records database statistics and five executions of each of 20 SPARQL queries. Results include mean, median, sample standard deviation and result counts. Timings include the HTTP request and JSON response parsing.

To repeat the measurements against an accessible endpoint:

```sh
pip install -r evaluation/requirements.txt
python evaluation/run_evaluation.py --stats --benchmark --output evaluation/results-new
```

See [evaluation methodology](evaluation/README.md) and [recorded execution times](evaluation/results/execution_metrics.csv).

## Tests

From `unistats-app/`:

```sh
python -m unittest discover -s tests -p "test_*.py"
npm ci
npm run check
npx playwright install chromium
npm run test:ui
```

Start the application on port 8000 before running the browser tests. Node.js is needed for frontend development and browser tests.

## Further documentation

- [Application and API](unistats-app/API.md)
- [AI integration](unistats-app/AI_INTEGRATION.md)
- [Dynamic data and filters](unistats-app/DYNAMIC_DATA.md)
- [Evaluation](evaluation/README.md)

Third-party frontend libraries retain their licenses in `unistats-app/public/vendor/`. Data coverage varies by dataset and academic year. Hosting the source on GitHub does not deploy the application: the Python backend needs a suitable server.

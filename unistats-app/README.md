# UniStats application

See the [repository README](../README.md) for setup, the source-data layout and evaluation instructions.

This folder contains the FastAPI backend, HTML/CSS/JavaScript UI, 41 SPARQL catalogue queries, runtime RDF snapshot and automated tests. Run `start.cmd` on Windows or install `requirements.txt` and start `uvicorn backend.main:app` from this folder.

No API credential is included. Copy `.env.example` to `.env` and supply your own settings if using hosted AI. All ordinary statistics, filters, charts and map functions are independent of having an AI provider key. Live database access and online map tiles require network connectivity.

- [API endpoints](API.md)
- [Hosted AI setup](AI_INTEGRATION.md)
- [Dynamic-data behavior](DYNAMIC_DATA.md)
- [Query evaluation](../evaluation/README.md)

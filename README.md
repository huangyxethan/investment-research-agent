# Investment Research Agent

A local, open-source Databricks-Genie-style NL2SQL agent for Chinese public-fund
investment research. Ask in natural language → get a narrative + chart + SQL + data.

See [`blueprint.md`](blueprint.md) for the full design (v3.0, grill-aligned).

## Repo layout

```
sql/schema.sql        Full operational + pgvector schema (auto-applied on PG first boot)
etl/                  Dagster assets + akshare/mootdx pulls (smoke_test_akshare.py for W1)
agent/                Vanna-based routed NL2SQL agent (W2+)
web/                  Streamlit frontend (W2+)
training_data/        Git-versioned RAG corpus: ddl/ docs/ qsql/
chart_catalog/        Jinja2 Plotly spec templates + _registry.yaml
docker-compose.yml    Local stack (W1: Postgres+pgvector only)
```

## Quickstart (W1 walking skeleton)

```bash
cp .env.example .env          # fill in PG_PASSWORD + LLM keys
docker compose up -d postgres # schema.sql auto-applies on first boot
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
python etl/smoke_test_akshare.py   # verify open-source data path
```

Verify schema loaded:

```bash
docker exec -it invest_pg psql -U postgres -d invest -c "\dt"
```

## Build order

Vertical slice first (blueprint §13): W1–W2 wire one NAV question end-to-end
(SQL → narrative → chart) to retire integration risk early, *then* add breadth.

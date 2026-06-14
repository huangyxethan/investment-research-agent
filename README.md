# Investment Research Agent

A Streamlit-based NL2SQL research assistant for Chinese public mutual-fund
analysis. Users ask questions in Chinese; the system generates PostgreSQL SQL,
executes it against a local fund database, and returns a short narrative,
Plotly chart, generated SQL, and raw data table.

This repository is an internship/demo project with production-oriented
engineering boundaries: ETL scripts, PostgreSQL schema, SQL safety rails,
role-gated UI, and evaluation scripts are kept separate.

For the full design rationale and roadmap, see [blueprint.md](blueprint.md).

## Current Scope

Implemented and usable today:

- Public-fund master data, NAV history, holdings, manager mappings, Shenwan
  industry classification, and precomputed fund performance metrics.
- Prompt-based Chinese NL2SQL over live PostgreSQL schema comments.
- SQL AST safety guard using `sqlglot`.
- Narrative generation from real query results.
- Plotly chart generation via LLM-authored chart specs with deterministic
  fallback rendering.
- Streamlit frontend with `admin` / `viewer` role behavior.
- NL2SQL and chart regression evaluation scripts.

Designed but not yet populated/fully wired:

- Vanna + pgvector RAG retrieval.
- Brinson attribution, RBSA factor exposure, convertible bond, futures,
  benchmark composition, fee, AUM/scale, and embedding data tables.
- Dagster production asset orchestration.
- Production-grade SSO, multi-tenant workspace isolation, and row-level access.

## Architecture

```text
AkShare / public web sources
        |
        v
ETL scripts in etl/
        |
        v
PostgreSQL 16 + pgvector (Docker container: invest_pg)
        |
        v
agent.pipeline
  - schema_context() reads table/column comments
  - LLM generates SQL
  - sql_guard validates SQL
  - run_sql() returns a DataFrame
  - narrate() creates Chinese analysis
  - chart renderer creates Plotly figure
        |
        v
Streamlit UI in web/app.py
```

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL 16, pgvector |
| Local runtime | Docker Compose |
| ETL | Python, AkShare, pandas, psycopg |
| Agent | Python, DeepSeek / Claude via API |
| SQL validation | sqlglot |
| Dataframe/charting | pandas, Plotly |
| Frontend | Streamlit, streamlit-authenticator |
| Evaluation | YAML test cases, Python regression scripts |

## Repository Layout

```text
agent/                 NL2SQL pipeline, LLM clients, SQL guard
etl/                   Data extraction, loading, performance computation, quality checks
eval/                  NL2SQL and chart regression tests
sql/schema.sql         PostgreSQL schema, comments, views, and functions
web/app.py             Streamlit frontend with auth and role behavior
training_data/qsql/    Curated Q-SQL examples; not dynamically retrieved yet
chart_catalog/         Early chart-template registry
scripts/               Utility scripts, e.g. password hash generation
docker-compose.yml     Local PostgreSQL + pgvector service
DEPLOY.md              Streamlit Cloud + Neon deployment guide
```

## Prerequisites

- Windows PowerShell or equivalent shell.
- Python 3.10+ recommended.
- Docker Desktop.
- A PostgreSQL-compatible connection string via `PG_CONN_STR`.
- At least one LLM API key:
  - `DEEPSEEK_API_KEY` for default/bulk use.
  - `ANTHROPIC_API_KEY` if using Claude.

## Configuration

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

Then fill in:

```dotenv
PG_CONN_STR=postgresql://postgres:change_me_local_dev@localhost:5432/invest
DEEPSEEK_API_KEY=...
ANTHROPIC_API_KEY=...
LLM_PROVIDER=deepseek
```

For the Streamlit app, create a local secrets file from the example:

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Generate password hashes with:

```powershell
.venv\Scripts\python.exe scripts\gen_password_hash.py <admin-password> <viewer-password>
```

Never commit `.env` or `.streamlit/secrets.toml`.

## Local Setup

Start PostgreSQL:

```powershell
docker compose up -d postgres
```

On first boot, Docker mounts `sql/schema.sql` into
`/docker-entrypoint-initdb.d/01_schema.sql`, so PostgreSQL creates the schema
automatically.

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Install app runtime dependencies:

```powershell
pip install -r requirements.txt
```

Install ETL/dev dependencies when loading or refreshing data:

```powershell
pip install -r requirements-etl.txt
```

Verify the database schema:

```powershell
docker exec invest_pg psql -U postgres -d invest -c "\dt"
```

## Quick Start: Local Web App

If your local database is already loaded, the shortest path to run the web UI is:

```powershell
docker compose up -d postgres
.venv\Scripts\streamlit.exe run web/app.py
```

Then open:

```text
http://localhost:8501
```

The app requires `.streamlit/secrets.toml` for login credentials and LLM/database
secrets. If you have not created it yet:

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Fill in `PG_CONN_STR`, at least one LLM key, `cookie_key`, and the password
hashes for the `admin` and `viewer` accounts.

If this is a fresh database with no data yet, run the ETL steps in
[Data Loading](#data-loading) before expecting useful answers.

## Data Loading

Run ETL scripts in this general order:

```powershell
.venv\Scripts\python.exe -m etl.build_universe
.venv\Scripts\python.exe etl/load_funds.py
.venv\Scripts\python.exe etl/load_managers.py
.venv\Scripts\python.exe etl/load_holdings.py
.venv\Scripts\python.exe etl/load_sw_industry.py
.venv\Scripts\python.exe -m etl.compute_fund_perf
.venv\Scripts\python.exe -m etl.data_quality
```

Notes:

- `load_funds.py` loads `dim_fund` and `fact_nav`.
- `load_holdings.py` loads `fact_holding` and stock rows in `dim_security`.
- `load_sw_industry.py` enriches `dim_security.industry_l1` and
  `dim_security.industry_l2`.
- `compute_fund_perf.py` computes YTD / 1Y / 3Y / SI metrics from accumulated
  NAV and writes `fact_fund_perf`.

## Run the Local Web App

```powershell
.venv\Scripts\streamlit.exe run web/app.py
```

The app will require valid Streamlit auth credentials from
`.streamlit/secrets.toml`. By default, Streamlit serves the app at:

```text
http://localhost:8501
```

Role behavior:

- `admin`: narrative, chart, generated SQL, and full data table.
- `viewer`: narrative, chart, and truncated data table; SQL hidden.

## Run the Agent from CLI

```powershell
.venv\Scripts\python.exe -m agent.pipeline "易方达蓝筹精选今年的单位净值走势"
```

This prints the generated SQL, row count, result sample, narrative, and chart
build status.

## Evaluation

Run the NL2SQL evaluation set:

```powershell
.venv\Scripts\python.exe -m eval.run_eval
```

Run deterministic chart regression checks:

```powershell
.venv\Scripts\python.exe -m eval.charts
```

Run data-quality checks:

```powershell
.venv\Scripts\python.exe -m etl.data_quality
```

## Deployment

The recommended low-cost demo path is:

- Streamlit Community Cloud for `web/app.py`.
- Neon PostgreSQL for the hosted database.
- Streamlit Secrets for database URL, LLM keys, cookie key, and password hashes.

See [DEPLOY.md](DEPLOY.md) for the full deployment procedure.

## Security Notes

- The LLM-generated SQL is validated before execution.
- Only a single `SELECT` statement is allowed.
- Allowed table prefixes are `dim_`, `fact_`, `bridge_`, and `v_`.
- Allowed table-valued functions are explicitly whitelisted.
- A `LIMIT 10000` row cap is injected if missing.
- Secrets are loaded from `.env` locally or Streamlit Secrets in deployment.

Security guardrails reduce execution risk, but they do not prove business
correctness. Business correctness is handled through schema design, semantic
views/functions, evaluation cases, and manual review.

## Known Limitations

- Data is based on public disclosure and historical NAV, not intraday data.
- Quarterly fund holdings are usually top-10 only; full-holding analysis uses
  semiannual/annual reports marked by `is_full_holding`.
- Industry classification is not 100% covered for HK stocks, BSE stocks,
  delisted names, and some historical holdings.
- Current NL2SQL does not dynamically retrieve Q-SQL examples from pgvector.
- Some schema tables are intentionally present as future extension points but
  currently empty.

## License

No license has been specified in this repository.

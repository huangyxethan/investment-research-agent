# Investment Research Agent

A Streamlit-based NL2SQL research assistant for Chinese public mutual-fund
analysis. Users ask questions in natural Chinese; the agent generates PostgreSQL
SQL against a live schema, validates it through deterministic safety rails,
executes it, and returns a short Chinese narrative, a Plotly chart, the generated
SQL, and the raw result table.

The system is built with production-oriented boundaries: ETL, schema, SQL
safety, role-gated UI, and evaluation are separate, independently runnable
layers, so each can be tested, replaced, or scaled on its own.

## Table of Contents

- [Design Principles](#design-principles)
- [Current Scope](#current-scope)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Local Setup](#local-setup)
- [Quick Start](#quick-start)
- [Data Loading](#data-loading)
- [Running the Application](#running-the-application)
- [Evaluation](#evaluation)
- [Deployment](#deployment)
- [Security Model](#security-model)
- [Known Limitations](#known-limitations)
- [License](#license)

## Design Principles

- **Semantic layer over prompt patches.** Recurring failure classes are fixed
  with views, whitelisted functions, and precomputed metric tables, not with
  longer prompts.
- **Deterministic safety in front of the LLM.** Generated SQL is parsed with
  `sqlglot` and must be a single read-only `SELECT` over whitelisted relations
  before it touches the database.
- **One verified calculation convention.** Returns, volatility, Sharpe, and
  drawdown are computed once in ETL with documented definitions, so the agent
  reads metrics instead of re-deriving them.
- **Evaluation-driven iteration.** Regression, chart-shape, latency, and stress
  suites tag results by capability, so weaknesses are visible and fixes are
  targeted.
- **Chinese-first product, English code.** Identifiers are English; schema
  comments, prompts, and the UI are Chinese because the LLM grounds on the
  comments and users ask in Chinese.

## Current Scope

**Implemented and usable today:**

- Public-fund master data, NAV history, holdings, manager mappings, Shenwan
  (申万) industry classification, and precomputed fund-performance metrics.
- Prompt-based Chinese NL2SQL grounded in live PostgreSQL schema/column comments.
- Deterministic SQL AST safety guard built on `sqlglot`.
- Chinese narrative generation from the actual query result.
- Plotly chart generation from LLM-authored chart specs, with a deterministic
  fallback renderer when a spec is missing or invalid.
- Custom date-range fund metrics via a whitelisted semantic-layer function
  (`f_fund_perf_range`).
- Streamlit frontend with `admin` / `viewer` role-based output.
- NL2SQL, chart, latency, and multi-dimension stress evaluation scripts.

**Designed, not yet populated or wired:**

- Vanna + pgvector RAG retrieval of Q-SQL examples.
- Brinson attribution, RBSA factor exposure, convertible bond, futures,
  benchmark composition, fee, and AUM/scale data tables.
- Dagster production asset orchestration.
- Production-grade SSO, multi-tenant isolation, and row-level access control.

## Architecture

```text
AkShare / public web sources
        │
        ▼
ETL scripts in etl/                         # extract → load → compute → quality-check
        │
        ▼
PostgreSQL 16 + pgvector  (Docker: invest_pg)
        │
        ▼
agent.pipeline.ask()                        # 5 timed stages
   1. schema_context()   read table/column comments        [DB]
   2. generate_sql()     LLM drafts a single SELECT         [LLM · bulk]
   3. sql_guard.guard()  AST validation + LIMIT injection   [local]
   4. run_sql()          execute, return a DataFrame        [DB]
   5. narrate()          Chinese analysis of the result     [LLM]
      author_chart_spec()→ render_spec() / build_chart()    [LLM · Claude → local]
        │
        ▼
Streamlit UI in web/app.py                  # auth + role-gated rendering
```

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL 16 with pgvector |
| Local runtime | Docker Compose |
| ETL | Python, AkShare, mootdx, pandas, psycopg |
| Agent | Python; Claude (Anthropic) + DeepSeek behind a single `LLMClient` interface |
| SQL safety | sqlglot AST validation |
| Charting | pandas, Plotly |
| Frontend | Streamlit, streamlit-authenticator |
| Evaluation | YAML cases + Python regression / profiling scripts |

LLM routing is tiered (see [Configuration](#configuration)): a `bulk` tier for
high-volume NL2SQL, a `primary` tier, and a `showcase` tier for chart-spec
authoring. All backends implement the same `complete()` surface in
[agent/clients.py](agent/clients.py), so the pipeline never depends on a vendor.

## Repository Layout

```text
agent/                 NL2SQL pipeline, LLM clients, SQL safety guard
  ├─ pipeline.py       ask(): schema → SQL → guard → exec → narrate → chart
  ├─ clients.py        LLMClient ABC + DeepSeek / Anthropic implementations
  └─ sql_guard.py      sqlglot AST validation and row-cap injection
etl/                   Extraction, loading, performance compute, quality checks
eval/                  NL2SQL, chart, latency, and stress evaluation
sql/schema.sql         PostgreSQL schema, comments, views, and functions
web/app.py             Streamlit frontend with auth and role-based output
training_data/qsql/    Curated Q-SQL few-shot examples (semantic layer)
chart_catalog/         Chart-template registry
scripts/               Utility scripts (e.g. password-hash generation)
docker-compose.yml     Local PostgreSQL + pgvector service
.env.example           Environment-variable template
```

## Prerequisites

- Docker Desktop (for the local PostgreSQL + pgvector container).
- Python 3.10 or newer.
- Commands below are shell-neutral. Activate the virtual environment with
  `source .venv/bin/activate` on macOS/Linux or `.venv\Scripts\activate` on
  Windows before running them.
- At least one LLM API key:
  - `DEEPSEEK_API_KEY` — default/bulk NL2SQL.
  - `ANTHROPIC_API_KEY` — Claude (primary tier + chart-spec authoring).

## Configuration

Create a local environment file from the template:

```bash
cp .env.example .env
```

Then fill it in. The variables map directly to
[.env.example](.env.example):

```dotenv
# --- Postgres ---
PG_PASSWORD=change_me_local_dev
PG_DB=invest
PG_USER=postgres
PG_CONN_STR=postgresql://postgres:change_me_local_dev@localhost:5432/invest

# --- LLM keys ---
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=

# --- Model routing ---
LLM_PRIMARY=claude-sonnet-4-6     # primary tier
LLM_BULK=deepseek-chat            # high-volume NL2SQL
LLM_SHOWCASE=claude-opus-4-7      # chart-spec authoring
```

`PG_PASSWORD`, `PG_DB`, and `PG_USER` are consumed by Docker Compose when the
container first boots; `PG_CONN_STR` is used by the ETL scripts and the agent.
The optional `LLM_PROVIDER` variable (`deepseek` by default, or `claude`)
selects which backend `agent.clients.default_client()` instantiates.

For the Streamlit app, create a local secrets file from its example:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Generate password hashes for the login accounts with:

```bash
python scripts/gen_password_hash.py <admin-password> <viewer-password>
```

> **Never commit `.env` or `.streamlit/secrets.toml`.** Both are git-ignored.

## Local Setup

**1. Start PostgreSQL.** On first boot, Docker mounts `sql/schema.sql` into
`/docker-entrypoint-initdb.d/`, so the schema is created automatically:

```bash
docker compose up -d postgres
```

**2. Create and activate a virtual environment:**

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

**3. Install dependencies.** The app runtime is kept slim (no torch/akshare) so
Streamlit Cloud builds fast; heavier ETL/ML/dev deps are separate:

```bash
pip install -r requirements.txt        # app runtime
pip install -r requirements-etl.txt    # only when loading/refreshing data
```

**4. Verify the schema:**

```bash
docker exec invest_pg psql -U postgres -d invest -c "\dt"
```

## Quick Start

If the database is already loaded, the shortest path to the web UI is:

```bash
docker compose up -d postgres
streamlit run web/app.py
```

Then open <http://localhost:8501>. The app requires `.streamlit/secrets.toml`
with `PG_CONN_STR`, at least one LLM key, a `cookie_key`, and the `admin` /
`viewer` password hashes.

If this is a fresh database with no data, run the [Data Loading](#data-loading)
steps first.

## Data Loading

Run the ETL pipeline in this order (module form, from the repo root):

```bash
python -m etl.build_universe       # fund universe → fund_universe.csv
python etl/load_funds.py           # dim_fund + fact_nav
python etl/load_managers.py        # manager mappings
python etl/load_holdings.py        # fact_holding + dim_security stocks
python etl/load_sw_industry.py     # dim_security.industry_l1 / _l2
python -m etl.compute_fund_perf    # YTD/1Y/3Y/SI → fact_fund_perf
python -m etl.data_quality         # post-load quality checks
```

**Universe maintenance (optional).** The fund universe can be cleaned and grown
without re-introducing share-class duplicates (e.g. the A/C/E/I shares of one
fund):

```bash
python -m etl.dedup_universe --dry-run   # analyze share-class dupes
python -m etl.dedup_universe --apply     # prune DB + CSV
python -m etl.expand_universe            # grow universe, delta codes
```

## Running the Application

### Web app

```bash
streamlit run web/app.py
```

Served at <http://localhost:8501>. Output is role-gated:

- **`admin`** — narrative, chart, generated SQL, and the full data table.
- **`viewer`** — narrative, chart, and a truncated table; SQL is hidden.

### Command line

Run the full pipeline against a single question and print each stage:

```bash
python -m agent.pipeline "易方达蓝筹精选今年的单位净值走势"   # "YTD unit-NAV trend of E Fund Blue Chip Select"
```

This prints the generated SQL, row count, a result sample, the narrative, and
the chart build status.

## Evaluation

```bash
python -m eval.run_eval    # NL2SQL regression over eval/cases.yaml
python -m eval.charts      # deterministic chart regression
python -m eval.perf        # per-stage latency profile (--repeat N)
python -m eval.stress      # multi-dimension stress over hard joins
python -m etl.data_quality # data-quality checks
```

- **`run_eval`** — NL2SQL correctness against curated cases.
- **`perf`** — runs the full `ask()` pipeline and breaks latency down into
  LLM / DB / local stages, answering "where does the time go".
- **`stress`** — drives hard, multi-table-join questions tagged by scope
  dimension (metric, company, performance, industry, cross-table), classifies
  each outcome (OK / EMPTY / EXEC_FAIL / GEN_FAIL / VALUE_FAIL), and reports
  per dimension to surface the weakest capability.

## Deployment

The recommended low-cost hosting path uses **Streamlit Community Cloud** (hosts
`web/app.py`) + **Neon** (hosted PostgreSQL with pgvector) +
**streamlit-authenticator** (login + the two roles). All data is public, so
there is no proprietary-data exposure.

**1. Create the Neon database.** Sign up at <https://neon.tech>, create a new
project (pick a nearby region), and copy the connection string:

```text
postgresql://USER:PASSWORD@ep-xxx.<region>.aws.neon.tech/neondb?sslmode=require
```

Neon supports pgvector by default — nothing extra to enable.

**2. Push local data into Neon.** With the local `invest_pg` container running,
dump and restore entirely inside the container to avoid host-side `psql`/encoding
issues (replace `<NEON_URL>`):

```bash
docker exec invest_pg sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges | psql "<NEON_URL>"'
docker exec invest_pg psql "<NEON_URL>" -c "SELECT count(*) FROM dim_fund;"   # spot check
```

Re-run this step whenever you refresh local data and want it reflected online.

**3. Generate password hashes** for the login accounts:

```bash
python scripts/gen_password_hash.py <admin-password> <viewer-password>
```

Copy the two `$2b$...` hashes (never commit plaintext passwords).

**4. Deploy on Streamlit Community Cloud.**

1. Sign in at <https://share.streamlit.io> with your GitHub account and authorize
   the repository.
2. **New app** → select the repo, branch `main`, and set
   **Main file path = `web/app.py`**.
3. Under **Advanced settings → Secrets**, paste the values following
   `.streamlit/secrets.toml.example` (Neon connection string, LLM key(s), the
   hashes from step 3, and a random `cookie_key`):

   ```toml
   PG_CONN_STR = "postgresql://...neon...?sslmode=require"
   DEEPSEEK_API_KEY = "sk-..."
   ANTHROPIC_API_KEY = "sk-ant-..."   # optional
   cookie_key = "a-long-random-string"

   [auth.credentials.usernames.admin]
   name = "管理员"
   email = "admin@example.com"
   password = "$2b$12$...admin-hash..."
   role = "admin"

   [auth.credentials.usernames.viewer]
   name = "访客"
   email = "viewer@example.com"
   password = "$2b$12$...viewer-hash..."
   role = "viewer"
   ```

4. **Deploy.** Streamlit Cloud installs the slim root `requirements.txt`
   (app runtime only, no torch/dagster) for a fast build. After a few minutes you
   get a public URL; verify the role difference by logging in as each account.

**Operational notes:**

- **Secrets** live only in Cloud Secrets, never in git (`.env` and
  `.streamlit/secrets.toml` are git-ignored).
- **Dependencies** — Cloud uses `requirements.txt`; local data/ETL work uses
  `requirements-etl.txt` (akshare/dagster/torch, etc.).
- **Free tier** sleeps when idle (a few seconds of cold start on next visit),
  which is acceptable for light use; move to a paid Render/Railway tier for
  frequent use.
- **Future** — swapping the frontend for FastAPI + React reuses Neon, accounts,
  secrets, and platform; only the UI layer changes, since `agent/` stays
  UI-agnostic.

## Security Model

SQL produced by the LLM is validated before execution by
[agent/sql_guard.py](agent/sql_guard.py):

- Exactly one statement, and it must be a read-only `SELECT` (no `SELECT … INTO`).
- Every referenced table must use an allowed prefix: `dim_`, `fact_`, `bridge_`,
  or `v_` (semantic-layer views); non-`public` schemas are rejected.
- Only explicitly whitelisted set-returning functions (currently
  `f_fund_perf_range`) may appear in a table position, including via `LATERAL`.
- A `LIMIT 10000` cap is injected if the query has none.
- Secrets come from `.env` locally or Streamlit Secrets in deployment.

These guardrails reduce execution risk but do not prove business correctness;
that is handled by schema design, semantic views/functions, evaluation cases,
and manual review.

## Known Limitations

- Data reflects public disclosure and historical NAV, not intraday quotes.
- Quarterly holdings are typically top-10 only; full-holding analysis relies on
  semiannual/annual reports flagged by `is_full_holding`.
- Industry classification is not fully covered for HK/BSE stocks, delisted
  names, and some historical holdings.
- NL2SQL does not yet dynamically retrieve Q-SQL examples from pgvector.
- Some schema tables exist as future extension points and are currently empty.

## License

Released under the [MIT License](LICENSE).

# Investment Research Data Agent — Project Blueprint (v2)

> A local, open-source alternative to Databricks Genie, purpose-built for Chinese public-fund investment research. Users ask questions in natural language; the system returns an analytical narrative, a chart, the generated SQL, and the raw query result — running on a personal laptop with a cloud LLM.

---

## 0. Document Status

| Field | Value |
|---|---|
| Version | v3.0 (grill-aligned: data口径, RBSA factors, vertical-slice roadmap) |
| Audience | The author (intern) + internship defense committee at a public mutual-fund company |
| Locked decisions | All Section 2 items are committed and should not be re-litigated without strong reason |
| Time horizon | 6–8 weeks of effective coding time |

---

## 1. Project Context and Goal

### 1.1 Context

This project is an **internship deliverable defended via PPT presentation** at a public mutual-fund (公募基金) company. The author's background is statistics + financial mathematics. The defense committee is expected to consist of quantitative researchers, product managers, and data engineers — all of whom will assess the work on **analytical correctness and architectural judgment**, not on UI polish or live demo latency.

Because the demo is delivered through PPT screenshots, **real-time end-to-end latency is invisible to evaluators**, but evaluators may probe latency, cost, and scalability in Q&A — so the architecture must include a defensible answer to those probes even when they are not directly demonstrated.

The deliverable the company expects is a **local agent** that reproduces Genie-like capability. Databricks itself is used only as a **development-time yardstick**: data quality is cross-validated by loading it into Databricks, and Databricks Genie serves as the reference benchmark the local agent is measured against. **Databricks is not part of the V1 architecture** — it is the bar to clear, not a dependency.

### 1.2 Goal

Build a domain-specific natural-language-to-SQL agent over a curated public-fund database. For every user question the system must produce four artifacts in one response:

1. **A natural-language analytical narrative** (Chinese).
2. **A polished, finance-domain-appropriate chart** (Plotly), not a generic table.
3. **The generated SQL** (collapsible, with brief explanation).
4. **The raw result set** (paginated, exportable).

The system must also support **iterative chart modification** through follow-up natural-language instructions (e.g. "switch to bar chart", "group by industry"), mirroring Databricks Genie's behaviour.

### 1.3 Non-Goals (Explicit V1 Out-of-Scope)

- Multi-tenant workspace isolation across investment teams.
- Row-level access control or RBAC.
- Real-time market data ingestion (intraday).
- Mobile UI.
- Production-grade SSO integration.

These belong to V2 and are documented in Section 16.

---

## 2. Strategic Decisions — Locked In

| # | Decision | Rationale |
|---|---|---|
| D1 | Project framed as **internship demo with production-grade architectural thinking** | Evaluators reward judgment over feature breadth. |
| D2 | **Single team, ~5–20 users, homogeneous role** for V1; multi-team isolation in V2 | Keeps auth/permissioning trivial without painting into a corner. |
| D3 | Effective dev budget **6–8 weeks**; design must accommodate V2 expansion without rewrites | Stated by the author; drives every component to be interface-based. |
| D4 | Data source: **akshare + mootdx**, no Wind / iFinD | Free, off-network, demo-portable. Internal data feeds abstracted behind a `DataSource` interface for V2. |
| D5 | Asset class coverage: **Equity (deep) + Convertible Bond (medium) + Futures (shallow)** | Schema covers all three; analytical depth concentrated on equity. |
| D6 | Equity analytical depth: **Brinson attribution (semi-annual full-holdings) primary + returns-based style exposure (RBSA) secondary** | Brinson computed on full holdings (半年报/年报) per industry practice; benchmark side approximated via 申万 industry indices; factor exposure via unconstrained rolling regression on spread factors — one 口径 serves both the radar and the style-drift heatmap. |
| D7 | LLM primary: **Claude Sonnet 4.6**; dev/iteration: **DeepSeek-V3**; showcase: **Claude Opus 4.7** | Performance-first, cost loose (≤ $200 total expected). |
| D8 | Embedding model: **local `bge-large-zh-v1.5`** | CPU-runnable; zero recurring cost; bilingual fluency. |
| D9 | Compute strategy: **Hot-path pre-compute + cold-path on-demand** (the so-called "Option B" with explicit storytelling) | Defensible architecture answer to latency / cost probes. |
| D10 | Visualization architecture: **Declarative spec + template catalog + LLM-as-spec-author** (Genie-style "C+") | LLM never writes rendering code; only emits JSON specs validated against a schema. |
| D11 | All cloud LLM, **laptop hardware does not constrain choice** | Personal Windows laptop with internet access; no local-inference dependency. |

---

## 3. Reference Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  Frontend — Streamlit (V1) → Next.js + FastAPI (V2 path)               │
│  Question box → narrative → chart → SQL → data table → feedback        │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │  HTTP / SSE
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Agent Orchestrator (Python)                                           │
│   ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐   │
│   │ Schema Retriever │  │ NL2SQL Generator │  │ Result Narrator    │   │
│   │ (Vanna + pgvec)  │→ │ (LLM Step A)     │→ │ (LLM Step B)       │   │
│   └──────────────────┘  └─────────┬────────┘  └────────────────────┘   │
│                                   │                                    │
│                                   ▼                                    │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │ Visualization Pipeline (C+ Declarative Spec)                 │     │
│   │  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐   │     │
│   │  │ Intent       │→ │ Spec Filler    │→ │ JSON-Schema     │   │     │
│   │  │ Classifier   │  │ (LLM Step C)   │  │ Validator       │   │     │
│   │  │ (LLM Step ↓) │  │ + Templates    │  │ + Renderer      │   │     │
│   │  └──────────────┘  └────────────────┘  └─────────────────┘   │     │
│   │   chart_catalog/{type}.json.j2  ──────────────►              │     │
│   └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │ Chart Modifier (follow-up turn)                              │     │
│   │  current_spec + user_instruction  ──►  LLM emits JSON Patch  │     │
│   │  → validator → re-render                                     │     │
│   └──────────────────────────────────────────────────────────────┘     │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │  parameterised SQL (read-only role)
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Data Layer — PostgreSQL 16 + pgvector                                 │
│  Operational schema  : dim_fund, dim_manager, fact_holding,            │
│                        fact_nav, dim_security,                         │
│                        fact_brinson_attribution (hot-path),            │
│                        fact_factor_exposure, dim_conv_bond,            │
│                        fact_conv_bond_holding, fact_futures_holding,   │
│                        dim_benchmark, fact_benchmark_composition       │
│  Embedding schema    : ddl_embeddings, doc_embeddings, qsql_embeddings │
└──────────────────────────────▲─────────────────────────────────────────┘
                               │
                  ┌────────────┴─────────────┐
                  │ ETL — Dagster Assets     │
                  │ Sources: akshare, mootdx │
                  │ Daily incremental        │
                  └──────────────────────────┘
```

---

## 4. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Storage | PostgreSQL 16 | Mature, free, sufficient for ~10 GB workload. |
| Vector store | pgvector | Co-located with operational data — one fewer service. |
| ETL orchestration | Dagster | Asset-based model fits dimensional warehousing; in place from day 1 even if assets are few. |
| Data sources | akshare, mootdx | Free, Chinese-market-native. |
| NL2SQL agent | Vanna.AI | RAG-over-schema architecture; library, not framework; clean to extend. |
| LLM primary | Claude Sonnet 4.6 | Top-tier SQL generation + Chinese fluency + instruction-following. |
| LLM dev/bulk | DeepSeek-V3 | 10× cheaper for high-volume iteration; excellent Chinese. |
| LLM showcase | Claude Opus 4.7 | Reserved for the most complex demo questions; ~5% of traffic. |
| Embedding | `bge-large-zh-v1.5` (local, CPU) | Free, bilingual, ~500 MB. |
| Chart library | Plotly (JSON-spec mode) | Declarative spec native; financial-domain styling possible. |
| Spec templating | Jinja2 | Standard, debuggable, parameter substitution. |
| Spec validation | `jsonschema` + Plotly schema | Hard guardrail on LLM output. |
| Frontend (V1) | Streamlit | Fastest path from agent to UI. |
| Frontend (V2 path) | Next.js + FastAPI | Pre-planned migration target. |
| Packaging | Docker Compose | One-command local stack. |
| Observability | Langfuse (optional, V2) | Trace every LLM call + SQL execution. |

---

## 5. Database Schema

The schema is intentionally **wide**: it includes tables for every asset class even when V1 ETL only fully populates the equity tables. This is a deliberate design move — the schema is itself an answer to the question "did you think about generality?" and the empty tables are V2 ETL targets.

### 5.1 Core Dimensions and Facts

| Table | Grain | Purpose | V1 Population |
|---|---|---|---|
| `dim_fund` | one row per fund | Basic info: type, inception, benchmark, currency | ✓ Full |
| `dim_manager` | one row per manager | Manager profile | ✓ Full |
| `bridge_fund_manager` | fund × manager × tenure (SCD2) | Historical management mapping | ✓ Full |
| `dim_security` | one row per security | Stocks, bonds, futures contracts | ✓ Full (stocks) |
| `dim_benchmark` | one row per benchmark | CSI 300, CSI 800, sector indices | ✓ Full |
| `fact_benchmark_composition` | benchmark × date × constituent | Used for Brinson | ✓ Full |
| `fact_holding` | fund × report_date × sec | Quarterly disclosed holdings | ✓ Full |
| `fact_nav` | fund × nav_date | Unit NAV, accumulated NAV, daily return | ✓ Full |
| `fact_fund_perf` | fund × period × as_of_date | Pre-computed Sharpe, drawdown, vol | ✓ Full |
| `dim_fund_scale` | fund × report_date | AUM, share, holder count | ✓ Full |
| `dim_fee` | one row per fund | Mgmt / custodian / sub / redemption | ✓ Full |

### 5.2 Equity Analytics (Hot-Path Pre-Computed)

| Table | Grain | Purpose |
|---|---|---|
| `fact_brinson_attribution` | fund × benchmark × period × sector | Allocation / Selection / Interaction effects |
| `fact_factor_exposure` | fund × as_of_date × factor | Returns-based (RBSA) style exposure: size, value, momentum, quality, volatility, growth — rolling-regression beta |
| `dim_factor` | one row per factor | Factor definition + the spread-index proxy used to construct it |

#### 5.2.1 Computation 口径 (grill-aligned)

**Brinson (primary, semi-annual):**
- **Frequency & holdings:** full-holdings only (半年报 6/30, 年报 12/31). 季报 top-10 is insufficient for the selection effect, so attribution is semi-annual.
- **Period alignment:** the prior full-holdings snapshot supplies opening weights; the attribution period is the following half-year (年报 12/31 → H1 returns; 半年报 6/30 → H2 returns).
- **Benchmark side:** benchmark sector weights and sector returns are approximated by 申万 Level-1 industry indices (market-cap weights + index returns) — see D6. No paid index-constituent weights required.
- **Accepted simplifications:** single-period static-weight assumption (a property of the single-period Brinson *model*, not a data gap — relaxing it needs Daniel-Grinblatt holding-based attribution, V2); static 申万 classification applied historically (data-availability driven); 复权 (adjusted) prices for security returns.
- **`holding_completeness` column** on `fact_brinson_attribution` records `full` vs `top10` so the data basis is auditable.

**Style exposure (secondary, returns-based / RBSA):**
- **Method:** unconstrained rolling OLS of fund return on **spread factors** (size = 小盘−大盘 index return, value = 价值−成长, …), so each beta reads directly as a tilt (sign = direction). Deliberately *not* Sharpe's constrained (≥0, sum=1) decomposition.
- **Window:** 60-trading-day rolling, monthly step → feeds both the factor radar (point-in-time) and the style-drift heatmap (time series) from one 口径.
- **Why not holdings-based Barra:** building 6 stock-level factor descriptors from akshare fundamentals is ~1–2 weeks and yields only semi-annual frequency; RBSA is cheap, frequency-rich, and reuses data we already have (NAV + style indices).

### 5.3 Convertible Bond Tables

| Table | Grain | Purpose |
|---|---|---|
| `dim_conv_bond` | one row per convertible | Issue terms, conversion ratio, put/call schedule |
| `fact_conv_bond_quote` | bond × trade_date | Price, parity (转股价值), bond floor (纯债价值), conv premium (转股溢价率), pure bond premium (纯债溢价率) — drives the 偏股/偏债 view |
| `fact_conv_bond_holding` | fund × report_date × bond | Fund-level convertible holdings |

### 5.4 Futures Tables

| Table | Grain | Purpose |
|---|---|---|
| `dim_futures_contract` | one row per contract | Multiplier, tick, margin, underlying |
| `fact_futures_quote` | contract × trade_date | OHLCV, OI, basis vs spot |
| `fact_futures_holding` | fund × report_date × contract | Disclosed (sparse) fund positions |

### 5.5 Modelling Rules

- **English identifiers, Chinese `COMMENT`.** ASCII identifiers stabilise LLM SQL generation; comments carry business meaning to the retriever.
- **All primary and foreign keys declared.** The agent uses them to infer joins.
- **Heavy metrics pre-computed.** Sharpe, drawdown, Brinson attribution, factor exposures live in `fact_*` tables.
- **SCD2 for time-varying relationships.** `bridge_fund_manager` answers "who managed fund X in 2023".
- **Partitioning** on `fact_nav`, `fact_conv_bond_quote`, `fact_futures_quote` by year.
- **`workspace_id` column** on every fact and dimension. V1 always populates `'default'`; V2 multi-tenancy reuses the same schema.

---

## 6. NL2SQL Agent

### 6.1 Why Vanna

Vanna's mechanism — retrieve relevant schema + similar past Q-SQL pairs from a vector store, hand them to an LLM, generate SQL, execute, summarise — mirrors Databricks Genie's internals. It is a library, not a framework, so customisation is straightforward.

### 6.2 Training Corpus

The training corpus is the single biggest accuracy lever. Three input types, all stored as embeddings in `pgvector`:

| Type | Target | Storage |
|---|---|---|
| DDL statements | one per table | `training_data/ddl/*.sql` |
| Documentation paragraphs | 40–60 (high-leverage: generalise to unseen questions) | `training_data/docs/*.md` |
| Question-SQL pairs | coverage-driven, not count-driven (see below) | `training_data/qsql/*.yaml` |

All training data is **git-versioned YAML/SQL/Markdown files**, not hardcoded Python — this is a V2-extensibility decision.

**North star = coverage + verification, not a pair count.** Vanna is RAG: it imitates the pairs it is fed, so an unverified-but-wrong SQL is *worse* than no pair (the model confidently reproduces the error). The corpus is built in three concentric rings under a hard anti-poisoning rule:

| Ring | Content | Size | Discipline |
|---|---|---|---|
| **Core** | demo / hero-chart questions | 12–15 | Hand-written, executed and result-checked, **must be 100% correct** — the defense rests on these. |
| **Coverage** | one exemplar per table + per join / aggregation / time-window pattern | 30–40 | LLM-drafted (DeepSeek) but **committed only after the author runs it against the DB and eyeballs the result**. |
| **Growth** | accrued from real usage during testing | open-ended | Same promotion discipline as Tier 3 charts (§7.6.3). |

Hard rule: **no LLM-drafted SQL enters the corpus until it has been executed and its result inspected.** Schema docs (DDL `COMMENT` + doc paragraphs) carry the generalisation load for questions unlike any training pair; Q-SQL pairs are point solutions.

### 6.3 Bootstrap Code

```python
from vanna.openai import OpenAI_Chat  # for DeepSeek (OpenAI-compatible)
from vanna.anthropic import Anthropic_Chat
from vanna.pgvector import PG_VectorStore

class RoutedAgent(PG_VectorStore):
    """Routes between Claude (default) and DeepSeek (bulk) by config."""
    def __init__(self, config):
        super().__init__(config)
        self.primary  = Anthropic_Chat(config={'model': 'claude-sonnet-4-6', ...})
        self.bulk     = OpenAI_Chat(config={'model': 'deepseek-chat',
                                            'base_url': 'https://api.deepseek.com', ...})
        self.showcase = Anthropic_Chat(config={'model': 'claude-opus-4-7', ...})

    def submit_prompt(self, prompt, **kwargs):
        backend = self._route(prompt, **kwargs)  # tag-based or env-driven
        return backend.submit_prompt(prompt, **kwargs)
```

### 6.4 SQL Safety Rails

| Rail | Mechanism |
|---|---|
| Read-only DB role | Agent connects as a Postgres user with `SELECT` only on operational schemas. |
| Statement timeout | `SET statement_timeout = '30s'` per session. |
| Row cap | Wrap every generated SELECT with `LIMIT 10000` if absent. |
| AST validation | Parse with `sqlglot`; reject if it touches non-whitelisted schemas, or contains `DELETE / UPDATE / DROP / TRUNCATE`. |
| Schema whitelist | Only `public.dim_*` and `public.fact_*` allowed. |
| Per-user rate limit | 60 questions / hour / user, even though "user" is always `default` in V1. |

---

## 7. Visualization Layer — Declarative Spec ("C+")

This is the architectural centrepiece for demo impact and the directly-borrowed Genie pattern.

### 7.1 Core Principle

> **The LLM only produces declarative JSON specs. It never produces rendering code. Specs are rendered by deterministic Python.**

Three benefits, in evaluator-pitching order:

1. **Reliability boundary is explicit** — invalid JSON is rejected and re-prompted; rendering itself cannot fail.
2. **Auditability** — every chart is reproducible from a stored spec without re-invoking the LLM.
3. **Cheap iteration** — chart modifications are JSON patches, not code rewrites.

### 7.2 Three-Tier Visualization Pipeline

The pipeline is intentionally split into **three tiers**. Tier 1 and Tier 2 use the chart catalog for polished, deterministic output on known patterns. Tier 3 is an open generator that lets the agent chart **any** reasonable user request, including ones no template anticipated. The tiered design is the architectural answer to "is your system generalizable, or just a fancy decision tree?".

```
User question + retrieved data schema + query result
           │
           ▼
    Step 1: Intent Classifier (LLM)
    Output: {"chart_type": "<id>", "tier": 1|2|3, "confidence": float, "rationale": "..."}
           │
   ┌───────┴───────────────────────────────────────────────────┐
   │                            │                              │
   ▼                            ▼                              ▼
 Tier 1 (Hero)              Tier 2 (Generic)              Tier 3 (Open)
 confidence ≥ 0.75 +        confidence ≥ 0.55 +           no template matched,
 hero template hit          generic template hit          or low confidence

 Step 2a: Spec Filler (LLM) on Jinja template      Step 2b: Free Spec Generator (LLM)
 → Plotly JSON                                     → Plotly JSON (no code)
   │                            │                              │
   └────────────────┬───────────┴──────────────────────────────┘
                    ▼
    Step 3: Validator (deterministic)
    - jsonschema validation
    - Plotly schema validation
    - Required-fields check
    Failure → retry once → fallback to data table
                    │
                    ▼
    Renderer (deterministic): plotly.io.from_json(spec).show()
```

The validator and renderer are **tier-agnostic** — a Tier 3 spec passes through the exact same JSON validation and rendering code as a Tier 1 spec. Likewise the chart modification flow (§7.4) operates on any tier's output.

### 7.3 Chart Catalog

```
chart_catalog/
├── _registry.yaml              # metadata + when-to-use, fed to Step 1 classifier
├── brinson_waterfall.json.j2
├── brinson_comparison_waterfall.json.j2
├── factor_radar.json.j2
├── nav_vs_benchmark.json.j2
├── conv_bond_premium_scatter.json.j2
├── futures_position_timeline.json.j2
├── time_series.json.j2         # generic fallback
├── stacked_bar.json.j2         # generic fallback
└── grouped_bar.json.j2         # generic fallback
```

#### `_registry.yaml` Example

```yaml
brinson_waterfall:
  description: "Brinson attribution waterfall: allocation / selection / interaction effects"
  use_when:
    - "User asks why fund X outperformed / underperformed benchmark"
    - "User asks to attribute returns to sector vs stock-picking"
  required_data_columns: [factor_name, contribution, cumulative]
  example_questions:
    - "易方达蓝筹精选去年跑赢沪深300的原因"
    - "Brinson 归因 张坤管理基金 vs CSI 300"
  styling:
    palette: "finance_red_green"
    locale: "zh-CN"
```

### 7.4 Chart Modification Pipeline

```
Current spec + follow-up instruction
           │
           ▼
    LLM emits JSON Patch (RFC 6902)
    e.g. [{"op": "replace", "path": "/data/0/type", "value": "bar"}]
           │
           ▼
    Apply patch via jsonpatch library
           │
           ▼
    Validator + Renderer (same as 7.2)
```

This pattern is what makes "switch to bar chart" / "color by sector" trivial and reliable.

### 7.5 Hero Charts (V1 Set, 10 Templates)

These are the polished, publication-quality charts that appear in PPT screenshots. Each is a Tier 1 template in the catalog. The author has explicitly prioritised **generalization over expanded specialisation** — see §7.6 — so the hero set ceiling is fixed at 10 and additional capacity goes into Tier 3.

| # | Chart | Asset Class | Type | Notes |
|---|---|---|---|---|
| 1 | Brinson attribution waterfall | Equity | Waterfall | Centrepiece. Math depth + visual clarity. |
| 2 | NAV vs benchmark cumulative return | Equity | Dual-axis line + shaded excess | Universally readable; sets up Brinson question. |
| 3 | Barra factor exposure radar | Equity | Radar | Showcases quantitative literacy. |
| 4 | Top-10 holdings industry sunburst | Equity | Sunburst | Two-level: 申万一级 → individual stock. |
| 5 | Convertible bond premium scatter (转股溢价率 vs 纯债溢价率, quadrant-labelled) | Convertible | Scatter + quadrant overlay | Differentiator; not a stock chart. |
| 6 | Convertible bond equity/bond tilt distribution (偏股/偏债) | Convertible | Histogram | Conversion-value-vs-price based; no option pricing. Core 转债实务 language. |
| 7 | Futures position timeline (主力合约 lifecycle) | Futures | Gantt | Demonstrates futures coverage given sparse data. |
| 8 | Futures net long/short exposure stacked | Futures | Stacked bar over time | Captures CTA-style directional positioning. |
| 9 | Comparison Brinson (two funds side-by-side) | Equity | Twin waterfall | "Wow" chart for the climactic demo question. |
| 10 | Manager style-drift heatmap | Equity | Time-series heatmap | Strong story for the "manager profile" demo question. |

Each template lives at `chart_catalog/<id>.json.j2` with a corresponding `_registry.yaml` entry feeding the intent classifier. The detailed data-column and parameter contract for each template lives in Appendix A.

> Workload note: ~0.5–1 day per template with LLM assistance (~7 days total). The savings vs the original 6-template plan come from Tier 3 absorbing demand for niche charts — not from cutting hero quality.

---

### 7.6 Generalization — The Open Generator (Tier 3)

The hero templates cover the demo's prepared questions, but evaluators routinely probe with **novel asks** the templates never anticipated ("draw the rolling 90-day correlation between fund X's NAV and CSI 300", "show me a violin plot of holding tenure"). A toy demo silently falls back to a data table here. A system that aspires to Genie-level capability must chart these too.

This is what Tier 3 solves. **It is treated as a first-class capability, not a graceful failure mode.**

#### 7.6.1 Design

When the intent classifier reports either no template match or confidence below threshold, control passes to the Free Spec Generator:

| Input | Source |
|---|---|
| User question | Original turn |
| Query result schema | Column names, types, cardinalities, row count |
| Sample rows | First 5–10 rows of the result |
| Output constraint | "Emit Plotly figure JSON. No Python. No commentary." |
| Few-shot exemplars | 3–5 prior Tier 3 successes from `chart_history` |
| Domain styling block | Shared `_styling.json` (palette, fonts, zh-CN locale) |

The LLM emits a complete Plotly JSON spec. The same `jsonschema` + Plotly schema validator from §7.2 catches malformed output. One retry on failure. Final fallback: render the raw data table with a brief explanation ("生成图表失败，已显示原始数据").

#### 7.6.2 Why This Is a Capability, Not a Workaround

| Without Tier 3 | With Tier 3 |
|---|---|
| Catalog defines what is chartable | Catalog defines what is *polished*; everything is chartable |
| Adding a chart = engineering work | Adding a chart = curating a successful spec into the catalog |
| Evaluator's unexpected question reveals fragility | Evaluator's unexpected question reveals adaptability |

#### 7.6.3 Promotion Loop (V1.5 path, low-cost)

Every Tier 3 success is logged with `(question, spec, user_feedback)`. When a Tier 3 chart pattern is hit ≥ 5 times with positive feedback, the author can promote it into the catalog by extracting it as a Jinja2 template. This turns the catalog into a **growing knowledge base** rather than a static manifest — and it is a concrete, defensible "how would you scale this" answer.

#### 7.6.4 Limits and Honest Caveats

- Tier 3 quality is meaningfully below Tier 1. Colours may be off, axis labels may be terse, edge cases (single-row results, all-zero series) can produce odd output.
- Tier 3 latency is higher (the LLM emits a longer JSON spec).
- For finance-domain charts requiring deep semantics (Brinson, transition matrices), Tier 3 is unreliable. Such charts should remain Tier 1.

These limits inform the **boundary the defense should articulate clearly**: hero questions get polished, exploratory questions get adequate, undefined territory degrades gracefully to data.

---

## 8. Analytics Computation Strategy — Hot vs Cold Path

### 8.1 The Hybrid Story

| Path | What | Latency | Mechanism |
|---|---|---|---|
| **Hot** | Brinson attribution, factor exposures, NAV-derived metrics, top-10 holdings, sector exposures, premium decomposition | < 1 s | Pre-computed by Dagster nightly; Agent issues `SELECT * FROM fact_*` |
| **Cold** | Cross-fund comparisons, user-defined windows, ad-hoc filters, novel ratio computations | 3–8 s | Agent generates SQL against base facts at request time |

### 8.2 Why Not Pure Pre-Compute (A)?

The space of possible analytical questions is unbounded; pre-computing every combination is infeasible.

### 8.3 Why Not Pure On-Demand (C)?

Brinson and factor decompositions are multi-step computations. Letting the LLM generate the SQL for them from scratch is error-prone. Pre-computing fixes the math once and verifies it offline.

### 8.4 The Evaluator-Facing Pitch

> "Hot queries are pre-computed by Dagster and read directly from `fact_brinson_attribution`-style tables, P50 < 1 s. Cold queries fall back to on-demand SQL generation, P95 5–8 s. We chose hybrid because pre-computing is bounded by question diversity and on-demand is bounded by LLM correctness. Production rollout would add a Redis result cache, bringing observed P95 below 2 s."

This paragraph is the answer to any latency / cost / scalability probe.

---

## 9. Frontend

### 9.1 V1 — Streamlit

```python
import streamlit as st
from agent import RoutedAgent

agent = RoutedAgent.from_config('config.yaml')

st.title("Investment Research Agent")
question = st.text_input("Ask a question about Chinese mutual funds")

if question:
    with st.spinner("Thinking..."):
        result = agent.ask(question)

    st.markdown("### 分析")
    st.write(result.narrative)
    st.plotly_chart(result.figure, use_container_width=True)

    with st.expander("Generated SQL"):
        st.code(result.sql, language="sql")
    with st.expander("Raw data"):
        st.dataframe(result.df, use_container_width=True)

    # Chart modification follow-up
    modification = st.text_input("Modify the chart (e.g. '改成柱状图')")
    if modification:
        new_figure = agent.modify_chart(result.spec, modification)
        st.plotly_chart(new_figure, use_container_width=True)
```

### 9.2 V2 — Next.js + FastAPI (deferred)

Stub-but-decoupled: V1 backend exposes a thin REST surface so V2 Next.js can swap the Streamlit layer without backend changes.

---

## 10. ETL Pipeline — Dagster

### 10.1 Asset Graph

```
akshare_raw_funds        ──►  staging_funds         ──►  dim_fund
akshare_raw_managers     ──►  staging_managers      ──►  dim_manager
                                                          ↓
                                                  bridge_fund_manager
akshare_raw_nav          ──►  staging_nav          ──►  fact_nav
akshare_raw_holdings     ──►  staging_holdings     ──►  fact_holding
mootdx_raw_quotes        ──►  staging_security_qt  ──►  dim_security
akshare_raw_benchmark    ──►  staging_benchmark    ──►  fact_benchmark_composition
                                                          ↓
                          ┌───────────────────────────────┘
                          ▼
                  fact_brinson_attribution   ◄── HOT-PATH PRE-COMPUTE
                  fact_factor_exposure       ◄── HOT-PATH PRE-COMPUTE
                  fact_fund_perf             ◄── HOT-PATH PRE-COMPUTE

akshare_raw_conv_bond    ──►  staging_conv_bond    ──►  dim_conv_bond
                                                          ↓
                                            fact_conv_bond_quote
                                            fact_conv_bond_holding
akshare_raw_futures      ──►  staging_futures      ──►  dim_futures_contract
                                                          ↓
                                            fact_futures_quote
                                            fact_futures_holding
```

### 10.2 Data Quality Checks

Every asset declares `dagster.AssetCheckSpec`:

- `fact_nav.daily_return` in `[-0.2, 0.2]`.
- `fact_holding.weight` sums to ≤ 1.05 per (fund, report_date).
- `dim_fund.fund_code` matches `^\d{6}$`.
- `fact_brinson_attribution`: allocation + selection + interaction ≈ active return (tolerance 1 bp).
- `fact_factor_exposure`: exposures within ±5 standard deviations.

Failed checks block downstream assets.

### 10.3 Scheduling

- Daily NAV refresh at 18:00 China time.
- Quarterly holdings refresh on file-arrival sensor.
- Brinson and factor recomputation triggered downstream of holdings refresh.

---

## 11. Architectural Discipline for V2 Extensibility

This section codifies the rules that protect future expansion. Every PR is reviewed against them.

| Discipline | V1 implementation | V2 unlocked |
|---|---|---|
| **LLM client interface** | `LLMClient` ABC with `submit_prompt`; routed implementation | Swap to local Qwen / Llama with one config change |
| **Vector store interface** | pgvector-backed; abstracted via Vanna's `VectorStore` | Swap to Chroma, Pinecone, Weaviate |
| **Data source interface** | `DataSource` ABC; akshare and mootdx as adapters | Plug in Wind / iFinD / 聚源 internal adapters |
| **DB role separation** | Agent uses read-only role from day 1 | RBAC and row-level security layer on top |
| **Auth middleware** | Stub returns `{"user": "default"}` | Plug in OAuth / OIDC / company SSO |
| **`workspace_id` everywhere** | Always `'default'` in V1 | Multi-team isolation by filtering on `workspace_id` |
| **Training data as files** | YAML / SQL / Markdown in git | UI for analysts to add curated Q-SQL pairs |
| **Dagster from day 1** | Even if only 6 assets | Adding assets is "extension", not "refactor" |
| **Chart catalog as templates** | Jinja2 files, not Python | UI for analysts to author new chart types |
| **Spec validation** | jsonschema with retry | Add LLM judge for "is this chart appropriate?" |

---

## 12. Deployment

### 12.1 docker-compose.yml (sketch)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: invest
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes: ["pgdata:/var/lib/postgresql/data"]

  dagster:
    build: ./etl
    environment:
      DAGSTER_PG_HOST: postgres
    depends_on: [postgres]

  agent-api:
    build: ./agent
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      PG_CONN_STR: postgresql://agent:...@postgres/invest
    depends_on: [postgres]

  frontend:
    build: ./web
    ports: ["8501:8501"]   # Streamlit
    depends_on: [agent-api]

volumes:
  pgdata: {}
```

### 12.2 Hardware

Author's personal Windows 11 laptop. Cloud LLM means local CPU / RAM consumption is dominated by Postgres and the embedding model, both lightweight. 8 GB RAM and 50 GB free disk are sufficient.

---

## 13. Implementation Roadmap (6–8 Weeks)

**Structuring principle — vertical slice first.** Rather than build all ETL, then all analytics, then the agent, then viz (breadth-first, with integration risk concentrated at the end), W1–W2 stand up the *thinnest possible end-to-end slice*: one table → one question → one SQL → one narrative → one chart. The hard integration points (Vanna + pgvector wiring, LLM routing, Plotly-spec render + JSON-schema validation, Streamlit) are thereby exercised in week 1–2 while ~6 weeks of buffer remain — not in W4–W5 where a snag would cascade into the only buffer week. This is the schedule-level expression of the same interface-first discipline in §11.

| Week | Goal | Deliverables |
|---|---|---|
| **W1** | Walking skeleton, part 1 | Docker stack running. Postgres + pgvector schema created with all tables (incl. V2 placeholders). akshare smoke test. `fact_nav` populated for a handful of funds. |
| **W2** | Walking skeleton, part 2 (thin slice) | Vanna + pgvector + routed LLM + Streamlit + one Plotly template **all wired end-to-end**: the system answers one real NAV question (SQL → narrative → line chart) with the validator in the loop. **Integration risk retired here.** |
| **W3** | Equity ETL breadth | Dagster assets for `dim_fund`, `dim_manager`, `fact_holding` (full-holdings). Fund universe populated (**target ~100, flexible — 20 clean funds beat 100 dirty ones**). Convertible & futures *staging* ETL kicked off here too, not deferred. |
| **W4** | Analytics layer | `fact_brinson_attribution` (semi-annual) + `fact_factor_exposure` (RBSA) pre-compute; quality checks pass; offline notebook validates the Brinson identity. |
| **W5** | Agent + training v1 | Routed agent hardened on the skeleton; **Core ring** (12–15 hand-verified Q-SQL pairs) + start of Coverage ring; narrative + SQL + table solid. |
| **W6** | Visualization (Tiers 1+3) | Three-tier pipeline; 6–8 of 10 hero templates; Tier 3 open generator functional with few-shot exemplars; chart modification working across tiers. |
| **W7** | Completion + polish | Remaining hero templates; convertible (偏股/偏债) + futures (1–2 示范 funds) charts; Coverage ring to ~40 verified pairs; styling finalised; PPT screenshots captured. |
| **W8** | Buffer + defense prep | Bug-fix buffer; final PPT polish; rehearsal Q&A; architectural-decision write-up. |

If weeks compress, drop in order: futures depth → convertible depth → hero templates 7/8/10 (futures/style-drift). Never drop: **the walking skeleton**, Brinson hot-path correctness, Tier 3 open generator, safety rails. Tier 3 is non-negotiable because it carries the generalization story.

---

## 14. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hallucinated column names | High at first | Strict schema retrieval + `sqlglot` AST validation + retry loop. |
| Brinson math errors | Medium | Offline notebook; **internal identity check** (allocation + selection + interaction = total active return) to within 1 bp. No external (Wind) ground truth exists — the 1 bp is self-consistency, not external reconciliation. |
| akshare API rate limits / instability | Medium | Local cache layer; tolerate 24-hour stale data; retry with backoff. |
| Convertible / futures data sparser than expected | High | Schema designed to tolerate sparse data; reduce scope to "schema present + 2-3 example funds" if needed. |
| Chart catalog incomplete by W7 | Medium | Cut comparison waterfall (#9) first; fallback to generic stacked bar. |
| LLM cost spirals on dev iteration | Low | DeepSeek for bulk; cache by question hash; budget alert at $100. |
| Defense Q&A probes a topic not in this doc | Medium | This document IS the prep material; every section answers a probable question. |

---

## 15. Success Metrics

- **Answer accuracy** — % of curated test questions where reviewer marks SQL as correct. Target ≥ 80% by W7, ≥ 90% by defense.
- **Chart appropriateness (Tier 1/2)** — % of templated questions where the chosen template matches the question intent. Target ≥ 90%.
- **Generalization rate (Tier 3)** — % of off-catalog questions that produce a valid, reasonable chart (reviewer-rated) rather than degrading to a data table. Target ≥ 70%. This is the headline metric for the generalization story.
- **Brinson correctness** — **internal identity** (allocation + selection + interaction vs total active return) reconciles < 1 bp on all test funds. This is a self-consistency check; no external ground truth is claimed.
- **Chart modification success** — % of "change to X" follow-ups that produce a valid, rendered chart, across all tiers. Target ≥ 95%.
- **End-to-end latency (informational)** — P50 < 5 s on hot path, P95 < 10 s on cold path. Not visible in PPT defense; reported as evidence of measurement discipline.
- **Total LLM cost** — under $200 across the project. Reported in defense as cost-awareness evidence.

---

## 16. V2 Roadmap / Future Work

Documented here so the defense can point to "we designed for these, not just deferred them":

1. **Multi-team workspace isolation** — leverage `workspace_id` already-threaded through the schema.
2. **Real-time market data** — extend ETL to streaming via Kafka or daily 15-minute pulls.
3. **Wind / iFinD internal data integration** — implement `DataSource` adapter; same agent code, richer data.
4. **Web frontend (Next.js + FastAPI)** — separation already in place via REST surface.
5. **Full Barra implementation** — cross-sectional regression, risk decomposition, customised factors.
6. **Bond fund analytics** — duration / convexity / credit decomposition; needs schema additions.
7. **QDII fund analytics** — currency overlay, foreign benchmark attribution.
8. **Analyst-authored training data** — UI for analysts to curate Q-SQL pairs without git.
9. **Result caching layer** — Redis cache keyed by question hash for repeat queries.
10. **LLM-judge quality scoring** — automated grading of agent answers using a separate LLM.
11. **Trade-level data (`fact_trade`)** — removed from V1 (公募 funds do not disclose trade-level data publicly); revisit if the company can provide an internal feed, unlocking turnover, trade attribution, and timing-ability analysis.

---

## Appendix A — Hero Chart Catalog (V1 Detail, 10 Templates)

| # | Template file | Required data columns | Required parameters |
|---|---|---|---|
| 1 | `brinson_waterfall.json.j2` | `factor_name, contribution, cumulative` | `fund_name, benchmark_name, period_label, palette` |
| 2 | `nav_vs_benchmark.json.j2` | `date, fund_nav, benchmark_nav` | `fund_name, benchmark_name` |
| 3 | `factor_radar.json.j2` | `factor_name, exposure_zscore` | `fund_name, as_of_date` |
| 4 | `holdings_industry_sunburst.json.j2` | `industry_l1, sec_name, weight` | `fund_name, report_date` |
| 5 | `conv_bond_premium_scatter.json.j2` | `bond_name, conv_premium, pure_bond_premium, size` | `as_of_date, quadrant_labels` |
| 6 | `conv_bond_tilt_histogram.json.j2` | `bond_name, conv_value, price, tilt_ratio` | `fund_name, as_of_date, bin_count` |
| 7 | `futures_position_timeline.json.j2` | `fund_code, contract_code, start_date, end_date, direction` | `fund_name` |
| 8 | `futures_net_exposure_stacked.json.j2` | `report_date, contract_group, long_notional, short_notional` | `fund_name` |
| 9 | `brinson_comparison_waterfall.json.j2` | `fund_name, factor_name, contribution` | `fund_a, fund_b, benchmark_name, period_label` |
| 10 | `manager_style_drift_heatmap.json.j2` | `report_date, factor_name, exposure_zscore` | `manager_name, fund_code` |

All templates use a shared `_styling.json` for palette, font, locale (`zh-CN`), red-up / green-down convention. Tier 3 open-generated specs also reference this styling block to keep visual consistency across catalog and generated charts.

---

## Appendix B — Comparable Open-Source Alternatives Considered

| Project | Verdict |
|---|---|
| **WrenAI** | Strong if a managed semantic layer (MDL) is desired; heavier than Vanna. Revisit if semantic layer becomes a bottleneck. |
| **DB-GPT** | Best Chinese-language support out of the box; full framework, less library-like. Considered but Vanna's smaller surface won. |
| **LangChain SQL Agent** | Too low-level; we would end up rebuilding Vanna. |
| **LlamaIndex NLSQLTableQueryEngine** | Same as LangChain. |
| **PandasAI** | DataFrame-first, not DB-first; misaligned with the persistent-warehouse model. |
| **Dataherald** | Comparable to Vanna; smaller community. |

**Decision recap: Vanna for V1 — minimal surface, RAG-over-schema mirrors Genie, custom routing implemented in <100 LOC. WrenAI is the V2 candidate if a formal semantic layer becomes necessary.**

---

## Appendix C — Defense Q&A Crib Sheet

Likely evaluator probes and one-paragraph answers — drawn from this document. Author should be fluent in all of these.

| Probe | Answer location |
|---|---|
| "How does this handle latency?" | §8.4 |
| "What if the LLM generates a destructive query?" | §6.4 |
| "Why not use Wind data?" | §2 (D4) + §11 (DataSource interface) |
| "Why didn't you do full Barra?" | §5.2 (simplified Barra rationale) + §16 (full Barra as V2) |
| "How would you support multiple teams?" | §11 (`workspace_id`) + §16 |
| "Why Plotly JSON specs instead of letting the LLM write code?" | §7.1 (three benefits) |
| "What if the user asks for a chart you didn't anticipate?" | §7.6 — Tier 3 open generator emits a fresh spec; same validator catches failures; data-table fallback as last resort. |
| "How would you grow the chart catalog without rewriting?" | §7.6.3 — promote frequently-used Tier 3 specs into Jinja templates. Catalog grows from real usage, not upfront design. |
| "How does this differ from Databricks Genie?" | Same RAG-over-schema pattern + same three-tier visualization design. We additionally specialise for Chinese mutual funds: domain-specific hero charts (Brinson, Barra, 转债 premium decomposition) and hot/cold pre-compute for finance metrics. Genie is SaaS; this is on-laptop. |
| "What's your accuracy?" | §15 metrics |
| "Why convertible bonds and futures?" | §2 (D5) — schema covers all three; depth concentrated on equity by data availability. |
| "Did you compare against real Databricks Genie?" | §1.1 — Genie is the reference benchmark; data quality cross-validated in Databricks; the deliverable is a local agent, Databricks is a yardstick not a dependency. |
| "Is your Brinson reconciled to Wind?" | §5.2.1 + §15 — no external ground truth; the 1 bp is an internal identity check (alloc + sel + int = active return); benchmark side is a 申万 industry-index approximation by design. |
| "Quarterly or semi-annual Brinson?" | §5.2.1 — semi-annual on full holdings; 季报 top-10 cannot support the selection effect. Opening weights from the prior full-holdings snapshot. |
| "Holdings-based or returns-based factor exposure?" | §5.2.1 — returns-based (RBSA), unconstrained rolling regression on spread factors; one 口径 for both radar and drift heatmap. |

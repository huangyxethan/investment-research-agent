-- =====================================================================
-- Investment Research Agent — Operational + Embedding Schema (v3.0)
-- =====================================================================
-- Modelling rules (blueprint §5.5):
--   * English identifiers, Chinese COMMENT (ASCII stabilises LLM SQL gen;
--     comments carry business meaning to the RAG retriever).
--   * All PK / FK declared so the agent can infer joins.
--   * workspace_id on every dim/fact; always 'default' in V1, the V2
--     multi-tenancy seam.
--   * Heavy metrics pre-computed into fact_* (hot path).
--   * fact_trade is intentionally ABSENT in V1 (公募 funds do not disclose
--     trade-level data publicly) — see blueprint §16 item 11.
--
-- This file is mounted into the Postgres container's
-- /docker-entrypoint-initdb.d so it runs automatically on first boot.
-- It is idempotent-ish (IF NOT EXISTS) so it is safe to re-apply.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================================
-- 1. CORE DIMENSIONS
-- =====================================================================

CREATE TABLE IF NOT EXISTS dim_fund (
    workspace_id   text        NOT NULL DEFAULT 'default',
    fund_code      char(6)     NOT NULL,
    fund_name      text        NOT NULL,
    fund_type      text,                       -- 股票型/混合型/债券型/QDII...
    inception_date date,
    benchmark_code text,                       -- 业绩比较基准 → dim_benchmark
    currency       text        DEFAULT 'CNY',
    is_active      boolean     DEFAULT true,
    PRIMARY KEY (workspace_id, fund_code)
);
COMMENT ON TABLE  dim_fund            IS '基金主数据，一只基金一行';
COMMENT ON COLUMN dim_fund.fund_code  IS '基金代码（6位数字）';
COMMENT ON COLUMN dim_fund.fund_type  IS '基金类型：股票型 / 混合型 / 债券型 / QDII 等';
COMMENT ON COLUMN dim_fund.benchmark_code IS '业绩比较基准代码，关联 dim_benchmark';

CREATE TABLE IF NOT EXISTS dim_manager (
    workspace_id text  NOT NULL DEFAULT 'default',
    manager_id   text  NOT NULL,
    manager_name text  NOT NULL,
    gender       text,
    education     text,
    start_work_date date,                       -- 从业起始日
    bio          text,
    PRIMARY KEY (workspace_id, manager_id)
);
COMMENT ON TABLE  dim_manager              IS '基金经理主数据，一位经理一行';
COMMENT ON COLUMN dim_manager.manager_name IS '基金经理姓名';

CREATE TABLE IF NOT EXISTS bridge_fund_manager (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    manager_id   text  NOT NULL,
    start_date   date  NOT NULL,               -- 任职起始（SCD2）
    end_date     date,                          -- 任职结束，NULL=至今
    is_current   boolean DEFAULT true,
    PRIMARY KEY (workspace_id, fund_code, manager_id, start_date),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, manager_id) REFERENCES dim_manager (workspace_id, manager_id)
);
COMMENT ON TABLE bridge_fund_manager IS '基金—经理任职关系（SCD2 拉链表），可回答"X基金2023年由谁管理"';

CREATE TABLE IF NOT EXISTS dim_security (
    workspace_id text NOT NULL DEFAULT 'default',
    sec_code     text NOT NULL,                 -- 股票/债券/期货标的代码
    sec_name     text,
    sec_type     text,                          -- stock / bond / future
    exchange     text,                          -- SSE / SZSE / ...
    industry_l1  text,                          -- 申万一级行业（静态分类，见 §5.2.1）
    industry_l2  text,
    list_date    date,
    delist_date  date,
    PRIMARY KEY (workspace_id, sec_code)
);
COMMENT ON TABLE  dim_security             IS '证券主数据：股票、债券、期货标的';
COMMENT ON COLUMN dim_security.industry_l1 IS '申万一级行业分类（V1 采用当前静态分类）';

CREATE TABLE IF NOT EXISTS dim_benchmark (
    workspace_id   text NOT NULL DEFAULT 'default',
    benchmark_code text NOT NULL,
    benchmark_name text,                         -- 沪深300 / 中证800 / 申万行业指数...
    PRIMARY KEY (workspace_id, benchmark_code)
);
COMMENT ON TABLE dim_benchmark IS '基准指数主数据：沪深300、中证800、申万行业指数等';

CREATE TABLE IF NOT EXISTS dim_factor (
    workspace_id  text NOT NULL DEFAULT 'default',
    factor_id     text NOT NULL,                 -- size / value / momentum / quality / volatility / growth
    factor_name   text,
    proxy_index   text,                          -- RBSA 用于代理该因子的价差指数（见 §5.2.1）
    description   text,
    PRIMARY KEY (workspace_id, factor_id)
);
COMMENT ON TABLE  dim_factor             IS '风格因子定义';
COMMENT ON COLUMN dim_factor.proxy_index IS '收益法(RBSA)中用于构造该因子的价差指数，如 size=小盘-大盘';

CREATE TABLE IF NOT EXISTS dim_fee (
    workspace_id   text NOT NULL DEFAULT 'default',
    fund_code      char(6) NOT NULL,
    mgmt_fee       numeric(6,4),                 -- 管理费率
    custodian_fee  numeric(6,4),                 -- 托管费率
    sub_fee        numeric(6,4),                 -- 申购费率
    redemption_fee numeric(6,4),                 -- 赎回费率
    PRIMARY KEY (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code)
);
COMMENT ON TABLE dim_fee IS '基金费率：管理费/托管费/申购费/赎回费';

CREATE TABLE IF NOT EXISTS dim_fund_scale (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    report_date  date NOT NULL,
    aum          numeric(20,2),                  -- 规模（元）
    total_share  numeric(20,2),                  -- 份额
    holder_count integer,                        -- 持有人户数
    PRIMARY KEY (workspace_id, fund_code, report_date),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code)
);
COMMENT ON TABLE dim_fund_scale IS '基金规模时序：AUM、份额、持有人户数';

-- =====================================================================
-- 2. CORE FACTS
-- =====================================================================

CREATE TABLE IF NOT EXISTS fact_benchmark_composition (
    workspace_id     text NOT NULL DEFAULT 'default',
    benchmark_code   text NOT NULL,
    as_of_date       date NOT NULL,
    constituent_code text NOT NULL,
    weight           numeric(10,6),              -- 成分权重
    PRIMARY KEY (workspace_id, benchmark_code, as_of_date, constituent_code),
    FOREIGN KEY (workspace_id, benchmark_code) REFERENCES dim_benchmark (workspace_id, benchmark_code)
);
COMMENT ON TABLE fact_benchmark_composition IS '基准成分及权重（Brinson 配置效应用）';

CREATE TABLE IF NOT EXISTS fact_holding (
    workspace_id  text NOT NULL DEFAULT 'default',
    fund_code     char(6) NOT NULL,
    report_date   date NOT NULL,                 -- 报告期（季报/半年报/年报）
    sec_code      text NOT NULL,
    weight        numeric(10,6),                 -- 占净值比例
    market_value  numeric(20,2),                 -- 持仓市值（元）
    shares        numeric(20,2),                 -- 持股数
    is_full_holding boolean DEFAULT false,       -- true=半年报/年报全持仓, false=季报前十大
    PRIMARY KEY (workspace_id, fund_code, report_date, sec_code),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code)
);
COMMENT ON TABLE  fact_holding                 IS '基金持仓明细。季报仅前十大重仓股；半年报/年报为全持仓';
COMMENT ON COLUMN fact_holding.is_full_holding IS 'true=全持仓(半年报/年报)，Brinson 仅使用全持仓';

-- fact_nav is RANGE-partitioned by year (blueprint §5.5). The partition
-- key (nav_date) must be part of the PK.
CREATE TABLE IF NOT EXISTS fact_nav (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    nav_date     date NOT NULL,
    unit_nav     numeric(12,4),                  -- 单位净值
    acc_nav      numeric(12,4),                  -- 累计净值
    daily_return numeric(10,6),                  -- 日收益率
    PRIMARY KEY (workspace_id, fund_code, nav_date),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code)
) PARTITION BY RANGE (nav_date);
COMMENT ON TABLE fact_nav IS '基金净值时序：单位净值、累计净值、日收益率（按年分区）';

DO $$
DECLARE y int;
BEGIN
  FOR y IN 2015..2026 LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS fact_nav_%s PARTITION OF fact_nav
         FOR VALUES FROM (%L) TO (%L);',
      y, format('%s-01-01', y), format('%s-01-01', y + 1));
  END LOOP;
END $$;

-- Catch-all partition: akshare NAV history reaches back to ~2001, earlier than
-- the explicit yearly partitions, so without a DEFAULT those inserts would fail.
CREATE TABLE IF NOT EXISTS fact_nav_default PARTITION OF fact_nav DEFAULT;

CREATE TABLE IF NOT EXISTS fact_fund_perf (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    period       text NOT NULL,                  -- 1M / 3M / 1Y / YTD / SI...
    as_of_date   date NOT NULL,
    total_return numeric(12,6),
    annual_return numeric(12,6),
    sharpe       numeric(10,4),
    max_drawdown numeric(10,6),
    volatility   numeric(10,6),
    PRIMARY KEY (workspace_id, fund_code, period, as_of_date),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code)
);
COMMENT ON TABLE fact_fund_perf IS '基金业绩指标（预计算）：收益、夏普、最大回撤、波动率';

-- =====================================================================
-- 3. EQUITY ANALYTICS (HOT-PATH PRE-COMPUTED)
-- =====================================================================

CREATE TABLE IF NOT EXISTS fact_brinson_attribution (
    workspace_id        text NOT NULL DEFAULT 'default',
    fund_code           char(6) NOT NULL,
    benchmark_code      text NOT NULL,
    period_start        date NOT NULL,           -- 期初快照日（年报12/31 或 半年报6/30）
    period_end          date NOT NULL,           -- 归因区间末
    sector              text NOT NULL,           -- 申万一级行业
    allocation_effect   numeric(12,8),           -- 配置效应
    selection_effect    numeric(12,8),           -- 选股效应
    interaction_effect  numeric(12,8),           -- 交互效应
    holding_completeness text DEFAULT 'full',    -- full / top10，标注数据基础
    PRIMARY KEY (workspace_id, fund_code, benchmark_code, period_start, period_end, sector),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, benchmark_code) REFERENCES dim_benchmark (workspace_id, benchmark_code)
);
COMMENT ON TABLE  fact_brinson_attribution IS 'Brinson 归因（半年频，全持仓）：配置/选股/交互效应。三项之和应等于总主动收益（内部恒等式校验<1bp）';
COMMENT ON COLUMN fact_brinson_attribution.period_start IS '期初持仓快照日；权重取自该日全持仓，归因区间为其后半年';

CREATE TABLE IF NOT EXISTS fact_factor_exposure (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    as_of_date   date NOT NULL,
    factor_id    text NOT NULL,
    exposure     numeric(12,6),                  -- 滚动回归 beta（暴露）
    exposure_zscore numeric(12,6),               -- 截面 z-score（雷达图/热力图用）
    PRIMARY KEY (workspace_id, fund_code, as_of_date, factor_id),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, factor_id) REFERENCES dim_factor (workspace_id, factor_id)
);
COMMENT ON TABLE fact_factor_exposure IS '收益法(RBSA)风格暴露：基金收益对价差因子的无约束滚动回归 beta（60日窗，月频）';

-- =====================================================================
-- 4. CONVERTIBLE BOND TABLES
-- =====================================================================

CREATE TABLE IF NOT EXISTS dim_conv_bond (
    workspace_id   text NOT NULL DEFAULT 'default',
    bond_code      text NOT NULL,
    bond_name      text,
    underlying_code text,                         -- 正股代码 → dim_security
    conv_price     numeric(12,4),                 -- 转股价
    conv_ratio     numeric(12,6),                 -- 转股比例
    issue_date     date,
    maturity_date  date,
    PRIMARY KEY (workspace_id, bond_code)
);
COMMENT ON TABLE dim_conv_bond IS '可转债主数据：转股价、转股比例、发行/到期、赎回回售条款';

CREATE TABLE IF NOT EXISTS fact_conv_bond_quote (
    workspace_id       text NOT NULL DEFAULT 'default',
    bond_code          text NOT NULL,
    trade_date         date NOT NULL,
    price              numeric(12,4),             -- 转债价格
    conv_value         numeric(12,4),             -- 转股价值（平价）
    bond_floor         numeric(12,4),             -- 纯债价值（债底）
    conv_premium       numeric(12,6),             -- 转股溢价率
    pure_bond_premium  numeric(12,6),             -- 纯债溢价率
    PRIMARY KEY (workspace_id, bond_code, trade_date),
    FOREIGN KEY (workspace_id, bond_code) REFERENCES dim_conv_bond (workspace_id, bond_code)
);
COMMENT ON TABLE fact_conv_bond_quote IS '可转债行情：价格、转股价值、纯债价值、转股溢价率、纯债溢价率（驱动偏股/偏债分析）';

CREATE TABLE IF NOT EXISTS fact_conv_bond_holding (
    workspace_id text NOT NULL DEFAULT 'default',
    fund_code    char(6) NOT NULL,
    report_date  date NOT NULL,
    bond_code    text NOT NULL,
    weight       numeric(10,6),
    market_value numeric(20,2),
    PRIMARY KEY (workspace_id, fund_code, report_date, bond_code),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, bond_code) REFERENCES dim_conv_bond (workspace_id, bond_code)
);
COMMENT ON TABLE fact_conv_bond_holding IS '基金可转债持仓';

-- =====================================================================
-- 5. FUTURES TABLES
-- =====================================================================

CREATE TABLE IF NOT EXISTS dim_futures_contract (
    workspace_id    text NOT NULL DEFAULT 'default',
    contract_code   text NOT NULL,
    contract_name   text,
    underlying       text,                         -- 标的（如 沪深300）
    multiplier      numeric(12,4),                 -- 合约乘数
    tick_size       numeric(12,6),
    margin_rate     numeric(8,6),
    PRIMARY KEY (workspace_id, contract_code)
);
COMMENT ON TABLE dim_futures_contract IS '期货合约主数据：乘数、最小变动、保证金、标的';

CREATE TABLE IF NOT EXISTS fact_futures_quote (
    workspace_id  text NOT NULL DEFAULT 'default',
    contract_code text NOT NULL,
    trade_date    date NOT NULL,
    open_px       numeric(14,4),
    high_px       numeric(14,4),
    low_px        numeric(14,4),
    close_px      numeric(14,4),
    volume        numeric(20,2),
    open_interest numeric(20,2),                   -- 持仓量
    basis         numeric(14,4),                   -- 基差（vs 现货）
    PRIMARY KEY (workspace_id, contract_code, trade_date),
    FOREIGN KEY (workspace_id, contract_code) REFERENCES dim_futures_contract (workspace_id, contract_code)
);
COMMENT ON TABLE fact_futures_quote IS '期货行情：OHLCV、持仓量、基差';

CREATE TABLE IF NOT EXISTS fact_futures_holding (
    workspace_id  text NOT NULL DEFAULT 'default',
    fund_code     char(6) NOT NULL,
    report_date   date NOT NULL,
    contract_code text NOT NULL,
    direction     text,                            -- long / short
    notional      numeric(20,2),                   -- 名义市值
    contracts     numeric(20,2),                   -- 手数
    PRIMARY KEY (workspace_id, fund_code, report_date, contract_code, direction),
    FOREIGN KEY (workspace_id, fund_code) REFERENCES dim_fund (workspace_id, fund_code),
    FOREIGN KEY (workspace_id, contract_code) REFERENCES dim_futures_contract (workspace_id, contract_code)
);
COMMENT ON TABLE fact_futures_holding IS '基金期货持仓（公开披露稀疏，仅对冲型基金有数据）';

-- =====================================================================
-- 6. EMBEDDING SCHEMA (pgvector; bge-large-zh-v1.5 → 1024 dims)
-- =====================================================================
-- NOTE: Vanna's PG_VectorStore may manage its own collection tables.
-- These are declared per blueprint §3; reconcile with Vanna wiring in W2.

CREATE TABLE IF NOT EXISTS ddl_embeddings (
    id         bigserial PRIMARY KEY,
    table_name text,
    content    text,
    embedding  vector(1024)
);
COMMENT ON TABLE ddl_embeddings IS 'DDL 语句向量（schema 检索）';

CREATE TABLE IF NOT EXISTS doc_embeddings (
    id        bigserial PRIMARY KEY,
    title     text,
    content   text,
    embedding vector(1024)
);
COMMENT ON TABLE doc_embeddings IS '业务文档段落向量（泛化杠杆）';

CREATE TABLE IF NOT EXISTS qsql_embeddings (
    id        bigserial PRIMARY KEY,
    question  text,
    sql_text  text,
    embedding vector(1024)
);
COMMENT ON TABLE qsql_embeddings IS '问题-SQL 对向量（核心准确率杠杆）';

-- Approximate-NN indexes (build after data load for better recall/perf)
CREATE INDEX IF NOT EXISTS ix_ddl_emb  ON ddl_embeddings  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_doc_emb  ON doc_embeddings  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_qsql_emb ON qsql_embeddings USING hnsw (embedding vector_cosine_ops);

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
COMMENT ON TABLE  dim_security             IS '证券主数据：股票、债券、期货标的（股票含申万一级 industry_l1、二级 industry_l2 分类）';
COMMENT ON COLUMN dim_security.industry_l1 IS '申万一级行业名称（共31个，如 银行、医药生物、电子、食品饮料、机械设备、电力设备、计算机、汽车、有色金属、非银金融 等）。按行业分析持仓时用 fact_holding JOIN dim_security ON sec_code，并配合 fact_holding.is_full_holding=TRUE（全持仓）。行业名用模糊匹配 industry_l1 LIKE ''%关键词%'' 以容忍用户简称（如"医药"匹配"医药生物"、"军工"匹配"国防军工"）。仅 A 股有值；港股及已退市标的为 NULL。V1 采用当前静态分类。';
COMMENT ON COLUMN dim_security.industry_l2 IS '申万二级行业名称（共131个，如 白酒Ⅱ、半导体、医疗器械、乘用车、汽车零部件、光伏设备、电力 等；隶属 industry_l1）。用 LIKE ''%关键词%'' 容忍简称及罗马数字后缀（如"白酒"匹配"白酒Ⅱ"）。仅 A 股有值；港股/北交所/退市为 NULL。V1 静态分类。';

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
COMMENT ON TABLE  fact_fund_perf               IS '基金业绩指标（预计算，权威口径）：收益率/夏普/最大回撤/波动率。指标类问题应查本表，勿用 fact_nav 现算';
COMMENT ON COLUMN fact_fund_perf.period        IS '区间：YTD(今年以来) / 1Y(近一年) / 3Y(近三年) / SI(成立以来)';
COMMENT ON COLUMN fact_fund_perf.total_return  IS '区间累计收益率（小数，0.60=60%），基于累计净值';
COMMENT ON COLUMN fact_fund_perf.annual_return IS '年化收益率（小数）';
COMMENT ON COLUMN fact_fund_perf.sharpe        IS '夏普比率（年化，无风险利率取0）';
COMMENT ON COLUMN fact_fund_perf.max_drawdown  IS '最大回撤（正数，0.48 表示回撤 48%）';
COMMENT ON COLUMN fact_fund_perf.volatility    IS '年化波动率（小数）';

-- Semantic-layer view: the clean surface the agent queries for metrics. It has
-- already picked the latest as_of per (fund, period), so there is NO date column
-- to mis-filter — removing a whole class of NL2SQL date-guessing errors.
CREATE OR REPLACE VIEW v_fund_perf_latest AS
SELECT p.fund_code, d.fund_name, p.period, p.total_return, p.annual_return,
       p.sharpe, p.max_drawdown, p.volatility
FROM fact_fund_perf p
JOIN dim_fund d ON p.fund_code = d.fund_code
WHERE p.as_of_date = (
    SELECT MAX(p2.as_of_date) FROM fact_fund_perf p2
    WHERE p2.fund_code = p.fund_code AND p2.period = p.period
);
COMMENT ON VIEW   v_fund_perf_latest IS '基金最新业绩指标（每基金每区间一行，已含 fund_name，已取最新交易日；查询无需也不应按日期过滤，也无需再 join dim_fund）。所有收益率/夏普/回撤/波动率/业绩排名问题查本视图';
COMMENT ON COLUMN v_fund_perf_latest.fund_name    IS '基金名称；"哪只基金/排名/展示"类结果请选此列（而非只给 fund_code，否则结果不可读）';
COMMENT ON COLUMN v_fund_perf_latest.period       IS '区间：YTD(今年以来)/1Y(近一年)/3Y(近三年)/SI(成立以来)';
COMMENT ON COLUMN v_fund_perf_latest.total_return IS '区间累计收益率（小数，0.60=60%）';
COMMENT ON COLUMN v_fund_perf_latest.max_drawdown IS '最大回撤（正数，0.48=回撤48%）';

-- Two complementary holding surfaces, clean separation of duties:
--   v_fund_holding_latest  = INDIVIDUAL STOCKS in each fund's latest snapshot
--   v_fund_industry_alloc  = INDUSTRY-level weights (all periods + is_latest flag)
-- v_fund_holding_latest: latest full-holding snapshot, one row per STOCK.
DROP VIEW IF EXISTS v_fund_holding_latest;
CREATE VIEW v_fund_holding_latest AS
SELECT h.fund_code, d.fund_name, h.report_date, h.sec_code, s.sec_name,
       s.industry_l1, s.industry_l2, h.weight, h.market_value, h.shares
FROM fact_holding h
JOIN (
    SELECT fund_code, MAX(report_date) AS rd
    FROM fact_holding WHERE is_full_holding GROUP BY fund_code
) m ON h.fund_code = m.fund_code AND h.report_date = m.rd
JOIN dim_fund d ON h.fund_code = d.fund_code
LEFT JOIN dim_security s ON h.sec_code = s.sec_code
WHERE h.is_full_holding;
COMMENT ON VIEW   v_fund_holding_latest IS '每只基金【最新一期】全持仓的【个股】明细（含 fund_name、sec_name、申万一级 industry_l1、申万二级 industry_l2、个股 weight）。回答"某基金最新持有哪些股票/某行业(一级或二级)里持有哪些个股(个股下钻)"用本视图。「前N大持仓/重仓股」（含最新与历年时序）请用 v_fund_top_holdings（holding_rank<=N）。行业【层面】的配置/占比/排名/时序：一级用 v_fund_industry_alloc，二级用 v_fund_industry_l2_alloc（不要在本视图臆造 industry_weight 列）。指定具体报告期（如"2025年报"）才查 fact_holding。';
COMMENT ON COLUMN v_fund_holding_latest.weight      IS '该个股占基金净值比例（小数，0.05=5%）；按 industry_l1 或 industry_l2 汇总即对应层级的配置占比';
COMMENT ON COLUMN v_fund_holding_latest.industry_l1 IS '所属申万一级行业；用 LIKE ''%关键词%'' 容忍简称（医药->医药生物）；NULL=港股/北交所/退市标的';
COMMENT ON COLUMN v_fund_holding_latest.industry_l2 IS '所属申万二级行业（共131个，如 白酒Ⅱ、半导体、医疗器械、乘用车 等）；用 LIKE ''%关键词%'' 容忍简称及罗马数字后缀（"白酒"匹配"白酒Ⅱ"）；NULL=港股/北交所/退市标的';

-- v_fund_industry_alloc: the single INDUSTRY-level surface. Per (fund, report_date,
-- industry) summed weight across full holdings, with an is_latest flag so the SAME
-- view serves both the time series (no filter) and the latest cross-section / ranking
-- (WHERE is_latest) — no correlated subquery needed. Also feeds Brinson.
DROP VIEW IF EXISTS v_fund_industry_alloc;
CREATE VIEW v_fund_industry_alloc AS
SELECT a.fund_code, a.fund_name, a.report_date, a.industry_l1, a.industry_weight,
       (a.report_date = MAX(a.report_date) OVER (PARTITION BY a.fund_code)) AS is_latest
FROM (
    SELECT h.fund_code, d.fund_name, h.report_date, s.industry_l1,
           SUM(h.weight) AS industry_weight
    FROM fact_holding h
    JOIN dim_fund d ON h.fund_code = d.fund_code
    JOIN dim_security s ON h.sec_code = s.sec_code
    WHERE h.is_full_holding AND s.industry_l1 IS NOT NULL
    GROUP BY h.fund_code, d.fund_name, h.report_date, s.industry_l1
) a;
COMMENT ON VIEW   v_fund_industry_alloc IS '基金各报告期（半年报/年报全持仓）的申万【一级】行业配置，每(基金,报告期,一级行业)一行。【仅一级行业】；二级行业(industry_l2)的配置/占比/排名/时序请用 v_fund_industry_l2_alloc。时间轴是 report_date（本视图【没有 period 列】，"近N年/某年以来"一律用 report_date 过滤，如 report_date>=''2023-06-01''，切勿写 period=''3Y''）。问"行业仓位随时间变化/趋势/历史/演变/近N年/堆叠折线|面积图"→不加 is_latest（时间序列）；问"最新行业配置分布/某行业占比最高/行业排名"→加 WHERE is_latest。已排除未归类标的（港股通/北交所）。';
COMMENT ON COLUMN v_fund_industry_alloc.is_latest      IS '该行是否为所属基金的最新一期全持仓；看最新截面/排名加 WHERE is_latest，看时序则不加';
COMMENT ON COLUMN v_fund_industry_alloc.industry_weight IS '该报告期内该申万一级行业的占净值比例之和（小数，0.38=38%），即绝对仓位；如需相对占比再按报告期归一化';

-- v_fund_industry_l2_alloc: the 申万 L2 sibling of v_fund_industry_alloc — same
-- is_latest pattern (latest cross-section/ranking vs time series), carries parent
-- industry_l1 too. Symmetric surface so L1/L2 industry questions route cleanly.
DROP VIEW IF EXISTS v_fund_industry_l2_alloc;
CREATE VIEW v_fund_industry_l2_alloc AS
SELECT a.fund_code, a.fund_name, a.report_date, a.industry_l1, a.industry_l2,
       a.industry_weight,
       (a.report_date = MAX(a.report_date) OVER (PARTITION BY a.fund_code)) AS is_latest
FROM (
    SELECT h.fund_code, d.fund_name, h.report_date, s.industry_l1, s.industry_l2,
           SUM(h.weight) AS industry_weight
    FROM fact_holding h
    JOIN dim_fund d ON h.fund_code = d.fund_code
    JOIN dim_security s ON h.sec_code = s.sec_code
    WHERE h.is_full_holding AND s.industry_l2 IS NOT NULL
    GROUP BY h.fund_code, d.fund_name, h.report_date, s.industry_l1, s.industry_l2
) a;
COMMENT ON VIEW   v_fund_industry_l2_alloc IS '基金各报告期（半年报/年报全持仓）的申万【二级】行业配置，每(基金,报告期,二级行业)一行（含父级 industry_l1、is_latest 标志）。【二级行业】的配置/占比/排名/时序一律用本视图；【一级行业】用 v_fund_industry_alloc。最新截面/排名加 WHERE is_latest；时序不加；时间轴 report_date（无 period 列，近N年用 report_date 过滤）。industry_l2 用 LIKE 容忍简称及罗马数字后缀（"白酒"匹配"白酒Ⅱ"）。已排除未归类标的（港股/北交所）。';
COMMENT ON COLUMN v_fund_industry_l2_alloc.is_latest      IS '是否为该基金最新一期；最新/排名加 WHERE is_latest，时序不加';
COMMENT ON COLUMN v_fund_industry_l2_alloc.industry_weight IS '该报告期该申万二级行业占净值比例之和（小数，0.38=38%）';

-- v_fund_top_holdings: per-stock holdings with a per-period RANK by weight, so
-- "前N大持仓/重仓股" is a trivial WHERE holding_rank<=N instead of a window
-- function the LLM tends to fake with a weight threshold. Carries is_latest so it
-- serves both the latest top-N and the historical top-N time series (stacked).
DROP VIEW IF EXISTS v_fund_top_holdings;
CREATE VIEW v_fund_top_holdings AS
SELECT h.fund_code, d.fund_name, h.report_date, h.sec_code, s.sec_name,
       s.industry_l1, h.weight, h.market_value,
       ROW_NUMBER() OVER (PARTITION BY h.fund_code, h.report_date
                          ORDER BY h.weight DESC NULLS LAST) AS holding_rank,
       (h.report_date = MAX(h.report_date) OVER (PARTITION BY h.fund_code)) AS is_latest
FROM fact_holding h
JOIN dim_fund d ON h.fund_code = d.fund_code
LEFT JOIN dim_security s ON h.sec_code = s.sec_code
WHERE h.is_full_holding;
COMMENT ON VIEW   v_fund_top_holdings IS '基金各报告期全持仓个股，带每期按权重降序的排名 holding_rank 与 is_latest 标志。「前N大持仓/前N大重仓股」一律用 holding_rank<=N（前十大 holding_rank<=10、前五大 holding_rank<=5）；「最新前N大」再加 WHERE is_latest；「成立以来/历年前N大」不加 is_latest（时间序列，可作堆叠图）。含个股名 sec_name、所属申万一级 industry_l1。';
COMMENT ON COLUMN v_fund_top_holdings.holding_rank IS '该个股在所属基金该报告期内按 weight 降序的名次，1=第一大重仓；「前N大」即 holding_rank<=N';
COMMENT ON COLUMN v_fund_top_holdings.weight       IS '个股占基金净值比例（小数，0.05=5%）';
COMMENT ON COLUMN v_fund_top_holdings.is_latest    IS '是否为该基金最新一期全持仓；看最新前N大加 WHERE is_latest，看历年时序则不加';

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

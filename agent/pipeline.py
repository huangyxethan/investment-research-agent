"""
W2 walking-skeleton pipeline: question -> SQL -> data -> narrative + chart.

Deliberately thin and Vanna-free (only one fact table exists yet). The schema
context is pulled live from Postgres comments, so this generalises as tables
fill in. Vanna slots in behind `clients.LLMClient` / a retriever later (§11).
"""
from __future__ import annotations

import datetime as dt
import decimal
import os
import re
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg
from dotenv import load_dotenv

from .clients import LLMClient, default_client
from .sql_guard import guard

load_dotenv()

# Tables exposed to the skeleton. Expand as ETL fills more (or hand to Vanna).
CONTEXT_TABLES = (
    "dim_fund",
    "dim_manager",
    "bridge_fund_manager",
    "fact_nav",
    "v_fund_perf_latest",
    "v_fund_holding_latest",
    "fact_holding",
    "dim_security",
)
TODAY = dt.date(2026, 6, 1)  # blueprint demo "current date"


# --------------------------------------------------------------------------- #
# schema context (the poor-man's retriever)
# --------------------------------------------------------------------------- #
def _conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["PG_CONN_STR"])


def schema_context(tables: tuple[str, ...] = CONTEXT_TABLES) -> str:
    """Render `table(col type -- comment)` blocks from live DB metadata."""
    blocks: list[str] = []
    with _conn() as conn, conn.cursor() as cur:
        for tbl in tables:
            cur.execute(
                """
                SELECT c.column_name, c.data_type,
                       col_description(%s::regclass, c.ordinal_position) AS comment
                FROM information_schema.columns c
                WHERE c.table_name = %s
                ORDER BY c.ordinal_position;
                """,
                (tbl, tbl),
            )
            cols = cur.fetchall()
            tbl_comment = ""
            cur.execute("SELECT obj_description(%s::regclass);", (tbl,))
            r = cur.fetchone()
            if r and r[0]:
                tbl_comment = f"  -- {r[0]}"
            lines = [f"TABLE {tbl}{tbl_comment}"]
            for name, dtype, comment in cols:
                c = f" -- {comment}" if comment else ""
                lines.append(f"  {name} {dtype}{c}")
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# NL -> SQL
# --------------------------------------------------------------------------- #
_SQL_SYS = """你是一个面向中国公募基金数据库的 PostgreSQL SQL 生成器。
规则：
- 只输出一条 SELECT 语句，不要 markdown、不要解释、不要代码围栏。
- 严格使用给定 schema 中的表名和列名。
- 基金名称在 dim_fund.fund_name，净值在 fact_nav，按 fund_code 关联。
- 基金/股票名称一律用模糊匹配 `fund_name LIKE '%关键词%'`：用户常用简称，而库里存的是全称（如"易方达蓝筹精选混合"）。经理名 dim_manager.manager_name 用精确匹配即可。
- 全持仓分析（如重仓股、行业、Brinson）只用 fact_holding.is_full_holding = TRUE（中报/年报全持仓）。
- 业绩指标（收益率/年化/夏普/最大回撤/波动率/按业绩排名）一律查视图 v_fund_perf_latest，按 period 选区间（YTD/1Y/3Y/SI），**不要用 fact_nav 现算，也不要按日期过滤**。
- 日期格式 'YYYY-MM-DD'。今天是 {today}。"今年"指 nav_date >= '{year}-01-01'。
- 净值表没有非交易日的行（元旦、周末等），**切勿假设某个确切日期存在并用等值连接**。
  计算区间收益时：「年初/期初」取区间内最早交易日的净值，「最新/当前」取 MAX(nav_date) 的净值；
  优先用窗口函数或 DISTINCT ON。YTD 收益率 = 最新累计净值 / 年初首个交易日累计净值 - 1。
- 时间序列按 nav_date 升序排序。

Schema:
{schema}"""


def generate_sql(question: str, llm: LLMClient, schema: str) -> str:
    raw = llm.complete(
        _SQL_SYS.format(today=TODAY.isoformat(), year=TODAY.year, schema=schema),
        question,
    )
    # strip accidental ```sql fences
    raw = re.sub(r"^```[a-zA-Z]*|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return guard(raw)


def run_sql(sql: str) -> pd.DataFrame:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    # psycopg returns NUMERIC as Decimal (object dtype) — coerce so charts/stats
    # see real numbers. Leave string codes (fund_code) untouched.
    for col in df.columns:
        s = df[col].dropna()
        if len(s) and isinstance(s.iloc[0], decimal.Decimal):
            df[col] = df[col].astype(float)
        elif "date" in col.lower():
            df[col] = pd.to_datetime(df[col])
    return df


# --------------------------------------------------------------------------- #
# narrative (grounded)
# --------------------------------------------------------------------------- #
_NARR_SYS = """你是一名投研分析师。根据提供的查询结果，用中文写 2-4 句分析。
只能使用结果中实际出现的数字，禁止编造。语气专业、简洁。"""


def narrate(question: str, df: pd.DataFrame, llm: LLMClient) -> str:
    if df.empty:
        return "查询无结果。"
    head = df.head(8).to_string(index=False)
    tail = df.tail(3).to_string(index=False) if len(df) > 8 else ""
    payload = (
        f"问题：{question}\n行数：{len(df)}\n列：{list(df.columns)}\n"
        f"前几行：\n{head}\n" + (f"末几行：\n{tail}\n" if tail else "")
    )
    return llm.complete(_NARR_SYS, payload, max_tokens=400).strip()


# --------------------------------------------------------------------------- #
# chart (deterministic; LLM-spec/Tier-3 is W6)
# --------------------------------------------------------------------------- #
def build_chart(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return go.Figure()
    date_cols = [c for c in df.columns if "date" in c.lower()]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in df.columns if c in ("fund_code", "fund_name")]

    if date_cols and num_cols:
        x = date_cols[0]
        y = num_cols[0]
        color = cat_cols[0] if cat_cols and df[cat_cols[0]].nunique() > 1 else None
        fig = px.line(df.sort_values(x), x=x, y=y, color=color, title=title)
    elif num_cols:
        fig = px.bar(df, x=df.columns[0], y=num_cols[0], title=title)
    else:
        fig = go.Figure()
    fig.update_layout(template="plotly_white", font=dict(family="Microsoft YaHei"))
    return fig


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
@dataclass
class AskResult:
    question: str
    sql: str
    df: pd.DataFrame
    narrative: str
    figure: go.Figure


def ask(question: str, llm: LLMClient | None = None) -> AskResult:
    llm = llm or default_client()
    schema = schema_context()
    sql = generate_sql(question, llm, schema)
    df = run_sql(sql)
    narrative = narrate(question, df, llm)
    figure = build_chart(df, question)
    return AskResult(question, sql, df, narrative, figure)


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "易方达蓝筹精选今年的单位净值走势"
    res = ask(q)
    print("=== SQL ===\n", res.sql)
    print("=== ROWS ===", len(res.df))
    print(res.df.head().to_string(index=False))
    print("=== NARRATIVE ===\n", res.narrative)
    print("=== FIGURE ===", "built" if res.figure.data else "empty")

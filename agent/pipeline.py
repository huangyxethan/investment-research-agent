"""
W2 walking-skeleton pipeline: question -> SQL -> data -> narrative + chart.

Deliberately thin and Vanna-free (only one fact table exists yet). The schema
context is pulled live from Postgres comments, so this generalises as tables
fill in. Vanna slots in behind `clients.LLMClient` / a retriever later (§11).
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import re
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg
from dotenv import load_dotenv

from .clients import LLMClient, default_client, make_client
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
    "v_fund_top_holdings",
    "v_fund_industry_alloc",
    "v_fund_industry_l2_alloc",
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
# chart — deterministic Tier-2 generic renderer (Tier-3 LLM-spec author is W6).
#
# Infers chart structure from the data SHAPE, not from hardcoded column names.
# This generalises one class at a time rather than per-question: any
# (time × category × value), (category × value), or (time × value) result.
# --------------------------------------------------------------------------- #

# value columns whose name signals a part-of-a-whole (→ stacked area over time,
# e.g. 行业/资产/板块 仓位), as opposed to a level (净值/价格 → overlaid lines).
_SHARE_HINTS = ("weight", "占比", "配置", "仓位", "比例", "ratio", "share", "pct", "占比例")


def _series_dimension(df: pd.DataFrame, date_cols, num_cols) -> str | None:
    """The categorical column that splits the data into series (lowest-cardinality
    non-date/non-numeric column with >1 value). Generalises the old hardcoded
    fund_code/fund_name to industry_l1, sec_name, manager, asset_class, ..."""
    cats = [c for c in df.columns if c not in date_cols and c not in num_cols
            and df[c].nunique() > 1]
    return min(cats, key=lambda c: df[c].nunique()) if cats else None


def _is_compositional(df: pd.DataFrame, value_col: str) -> bool:
    """Parts-of-a-whole (stack) vs levels (overlay). Name hint OR values bounded
    in [0,1] — keeps NAV (values ~1-8) as overlaid lines, weights as stacks."""
    if any(h in value_col.lower() for h in _SHARE_HINTS):
        return True
    s = df[value_col].dropna()
    return len(s) > 0 and bool(s.between(0, 1.2).mean() > 0.9)


# explicit chart-type requests in the question, honored when the data fits.
# A tiny intent layer (the full Tier-3 LLM-spec author is W6); generalises to any
# "用饼图/柱状图/折线图/面积图展示…" phrasing, not one hard-coded question.
# order matters: 'area' is checked before 'line' so "堆叠折线图" → stacked area
# (it contains both 堆叠 and 折线; the 堆叠 intent wins).
_CHART_KEYWORDS = {
    "pie":     ("饼图", "饼状", "扇形", "占比图", "pie"),
    "scatter": ("散点", "气泡", "scatter"),
    "area":    ("面积", "堆叠", "堆积", "area", "stacked"),
    "line":    ("折线", "线图", "曲线", "line"),
    "bar":     ("柱状", "条形", "柱形", "bar"),
}


def _requested_chart(text: str) -> str | None:
    t = (text or "").lower()
    for kind, kws in _CHART_KEYWORDS.items():
        if any(k.lower() in t for k in kws):
            return kind
    return None


# in-place chart edits ("改成红色" / "换柱状图") — applied to the SAME data with
# no re-query / no LLM call, so the demo feels like Genie's live tweaking.
_COLOR_MAP = {
    "红色": "red", "红": "red", "蓝色": "blue", "蓝": "blue", "绿色": "green", "绿": "green",
    "橙色": "orange", "橙": "orange", "黄色": "gold", "黄": "gold", "紫色": "purple", "紫": "purple",
    "黑色": "black", "黑": "black", "灰色": "gray", "灰": "gray", "青色": "cyan", "粉色": "pink",
}
_PIE_SCALE = {"red": "Reds", "blue": "Blues", "green": "Greens", "orange": "Oranges",
              "purple": "Purples", "gray": "Greys"}
_EDIT_VERBS = ("改成", "改为", "换成", "换", "变成", "变为", "调成", "弄成", "改", "设成")


def parse_chart_edit(text: str) -> dict:
    """Extract {chart_type?, color?} from a modify instruction."""
    edit: dict = {}
    ct = _requested_chart(text)
    if ct:
        edit["chart_type"] = ct
    for word, css in _COLOR_MAP.items():
        if word in (text or ""):
            edit["color"] = css
            break
    return edit


def is_chart_edit(text: str) -> bool:
    """True if `text` is an in-place chart tweak (edit verb + a type/color token)."""
    return bool(parse_chart_edit(text)) and any(v in (text or "") for v in _EDIT_VERBS)


def _apply_color(fig: go.Figure, color: str) -> None:
    css = _COLOR_MAP.get(color, color)
    if not fig.data:
        return
    head = fig.data[0]
    if isinstance(head, go.Pie):  # monochrome shades across slices, else leave default
        scale = _PIE_SCALE.get(css)
        labels = head.labels
        if scale and labels is not None and len(labels):
            stops = [i / max(len(labels) - 1, 1) for i in range(len(labels))]
            fig.update_traces(marker=dict(colors=px.colors.sample_colorscale(scale, stops)))
    elif isinstance(head, go.Bar):
        fig.update_traces(marker_color=css)
    else:  # Scatter (line / area)
        fig.update_traces(line_color=css, marker_color=css)


def _pie(df: pd.DataFrame, names: str, values: str, title: str,
         max_slices: int = 10) -> go.Figure:
    """Composition pie: drop ~zero slices, fold the long tail into 其他."""
    d = df[[names, values]].copy()
    d = d[d[values] > 1e-9].sort_values(values, ascending=False)
    if len(d) > max_slices:
        head = d.iloc[:max_slices]
        tail = pd.DataFrame({names: ["其他"], values: [d.iloc[max_slices:][values].sum()]})
        d = pd.concat([head, tail], ignore_index=True)
    return px.pie(d, names=names, values=values, title=title)


def build_chart(df: pd.DataFrame, title: str, *,
                chart_type: str | None = None, color: str | None = None) -> go.Figure:
    """chart_type / color override the question's own intent — used by the
    in-place "改图" interaction (re-render same data with new params)."""
    if df.empty:
        return go.Figure()
    date_cols = [c for c in df.columns
                 if pd.api.types.is_datetime64_any_dtype(df[c]) or "date" in c.lower()]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    dim = _series_dimension(df, date_cols, num_cols)
    y = num_cols[0] if num_cols else None
    single_period = not date_cols or df[date_cols[0]].nunique() <= 1

    fig = None
    # 1) honor an explicit chart-type (override > question intent), if feasible
    want = chart_type or _requested_chart(title)
    if y is not None:
        if want == "pie" and dim and single_period:
            fig = _pie(df, dim, y, title)
        elif want == "scatter" and len(num_cols) >= 2:
            fig = px.scatter(df, x=num_cols[0], y=num_cols[1], hover_name=dim, title=title)
        elif want == "bar":
            fig = px.bar(df, x=(dim or df.columns[0]), y=y, title=title)
        elif want == "line":
            x = date_cols[0] if date_cols else (dim or df.columns[0])
            fig = px.line(df.sort_values(x), x=x, y=y,
                          color=(dim if date_cols else None), title=title)
        elif want == "area" and (date_cols or dim):
            x = date_cols[0] if date_cols else df.columns[0]
            fig = px.area(df.sort_values(x), x=x, y=y, color=dim, title=title)

    # 2) else infer chart structure from the data shape
    if fig is None:
        if date_cols and num_cols:
            x = date_cols[0]
            d = df.sort_values(x)
            if dim and _is_compositional(df, y):
                # composition over time → stacked area (仓位结构演变).
                keep = [c for c, g in d.groupby(dim) if g[y].abs().max() > 1e-9]
                fig = px.area(d[d[dim].isin(keep)], x=x, y=y, color=dim, title=title)
            else:
                fig = px.line(d, x=x, y=y, color=dim, title=title)  # 净值走势/对比
        elif len(num_cols) >= 2:
            # two metrics, no time axis → scatter (风险-收益 等指标关系).
            fig = px.scatter(df, x=num_cols[0], y=num_cols[1], hover_name=dim, title=title)
        elif num_cols:
            fig = px.bar(df, x=(dim or df.columns[0]), y=y, title=title)  # 排名/分解
        else:
            fig = go.Figure()
    if color:
        _apply_color(fig, color)
    fig.update_layout(template="plotly_white", font=dict(family="Microsoft YaHei"))
    return fig


# --------------------------------------------------------------------------- #
# Tier-3 — LLM-authored declarative chart spec (blueprint §7, D10).
#
# The LLM emits a constrained JSON spec (chart type + column encodings), NOT
# rendering code; we validate it against the actual result columns and render it
# with our own engine. Invalid/failed spec → fall back to the Tier-2 build_chart.
# Same discipline as NL2SQL: model proposes, we validate, deterministic execute.
# --------------------------------------------------------------------------- #
_CHART_TYPES = ("line", "bar", "area", "pie", "scatter")

_SPEC_SYS = """你是图表规格生成器。根据【用户问题】和【数据列】，为结果选出最合适的图表，
只输出一个 JSON 对象，不要解释、不要 markdown 代码围栏。

JSON 字段（schema）：
- chart_type: "line"|"bar"|"area"|"pie"|"scatter"
- x: 列名（pie 表示扇区类别，scatter/line/bar 的横轴；x/y 取值必须是给定列名之一）
- y: 列名（pie 表示数值，其余为纵轴数值）
- color: 列名 或 null（按该列分组着色，如按基金类型/行业分色；散点可按某指标着色）
- size: 列名 或 null（仅 scatter，气泡大小，须为非负数值列）
- orientation: "v" 或 "h"（仅 bar；条目多/名称长时用 "h" 横向更易读）
- stack: true/false（仅 area/bar，是否堆叠）
- normalize: "percent" 或 null（百分比/100%堆叠，看“相对占比/构成”时用）
- sort: null 或 {"by":"列名","dir":"asc"|"desc"}（排名类按数值降序）
- title: 简短中文标题

选型规则：
- 时间序列(含日期列)→ line；行业/资产仓位随时间且看构成 → area(可 stack/normalize)
- 两个数值指标的关系(如收益vs波动)→ scatter，可用 color/size 增加维度
- 单期占比/构成 → pie；排名/对比 → bar(按值排序，条目多用 orientation:h)
- x/y/color/size 只能引用给定列名，绝不杜撰列名。"""


def _dtype_kind(s: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    return "text"


def _label_col(df: pd.DataFrame, used) -> str | None:
    for c in df.columns:
        if c not in used and not pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None


def _validate_spec(spec, df: pd.DataFrame) -> bool:
    if not isinstance(spec, dict):
        return False
    if spec.get("chart_type") not in _CHART_TYPES:
        return False
    cols = set(df.columns)
    if spec.get("x") not in cols or spec.get("y") not in cols:
        return False
    for f in ("color", "size"):
        v = spec.get(f)
        if v is not None and v not in cols:
            return False
    return True


def render_spec(df: pd.DataFrame, spec: dict, *, recolor: str | None = None) -> go.Figure:
    """Render a validated chart spec to a Plotly figure (the single renderer)."""
    t = spec["chart_type"]
    x, y = spec["x"], spec["y"]
    color, size = spec.get("color"), spec.get("size")
    title = spec.get("title") or ""
    d = df
    srt = spec.get("sort")
    if isinstance(srt, dict) and srt.get("by") in d.columns:
        d = d.sort_values(srt["by"], ascending=(srt.get("dir") == "asc"))

    if t == "pie":
        fig = _pie(d, x, y, title)
    elif t == "scatter":
        if size and (not pd.api.types.is_numeric_dtype(d[size]) or (d[size] < 0).any()):
            size = None  # px.scatter size must be non-negative numeric
        fig = px.scatter(d, x=x, y=y, color=color, size=size,
                         hover_name=_label_col(d, {x, y, color, size}), title=title)
    elif t == "line":
        fig = px.line(d.sort_values(x), x=x, y=y, color=color, title=title)
        if spec.get("normalize") == "percent" and color:
            fig.update_traces(groupnorm="percent")
    elif t == "area":
        fig = px.area(d.sort_values(x), x=x, y=y, color=color, title=title)
        if spec.get("normalize") == "percent":
            fig.update_traces(groupnorm="percent")
    elif t == "bar":
        if spec.get("orientation") == "h":
            fig = px.bar(d, x=y, y=x, color=color, orientation="h", title=title)
        else:
            fig = px.bar(d, x=x, y=y, color=color, title=title)
        if color or spec.get("stack"):
            fig.update_layout(barmode="stack" if spec.get("stack") else "group")
        if spec.get("normalize") == "percent":
            fig.update_layout(barnorm="percent")
    else:
        raise ValueError(f"unknown chart_type {t}")

    if recolor:
        _apply_color(fig, recolor)
    fig.update_layout(template="plotly_white", font=dict(family="Microsoft YaHei"))
    return fig


def _spec_client() -> LLMClient:
    """Claude for spec authoring (stronger structured output); DeepSeek fallback."""
    try:
        return make_client("claude")
    except Exception:
        return default_client()


def author_chart_spec(question: str, df: pd.DataFrame,
                      llm: LLMClient | None = None) -> dict | None:
    """Tier-3: ask the LLM for a chart spec; return it only if valid, else None."""
    if df.empty or len(df.columns) < 2:
        return None
    llm = llm or _spec_client()
    cols = ", ".join(f"{c}:{_dtype_kind(df[c])}" for c in df.columns)
    payload = (f"问题：{question}\n数据列：{cols}\n样例（前3行）：\n"
               f"{df.head(3).to_string(index=False)}")
    try:
        raw = llm.complete(_SPEC_SYS, payload, max_tokens=300)
        raw = re.sub(r"^```[a-zA-Z]*|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        spec = json.loads(raw)
    except Exception:
        return None
    return spec if _validate_spec(spec, df) else None


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
    spec: dict | None = None   # Tier-3 chart spec (None when Tier-2 fallback used)


def ask(question: str, llm: LLMClient | None = None) -> AskResult:
    llm = llm or default_client()
    schema = schema_context()
    sql = generate_sql(question, llm, schema)
    df = run_sql(sql)
    narrative = narrate(question, df, llm)
    spec = author_chart_spec(question, df)          # Tier-3 (Claude); None on failure
    try:
        figure = render_spec(df, spec) if spec else build_chart(df, question)
    except Exception:
        figure, spec = build_chart(df, question), None   # graceful fallback
    return AskResult(question, sql, df, narrative, figure, spec)


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "易方达蓝筹精选今年的单位净值走势"
    res = ask(q)
    print("=== SQL ===\n", res.sql)
    print("=== ROWS ===", len(res.df))
    print(res.df.head().to_string(index=False))
    print("=== NARRATIVE ===\n", res.narrative)
    print("=== FIGURE ===", "built" if res.figure.data else "empty")

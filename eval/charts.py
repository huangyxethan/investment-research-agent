"""
Chart-shape regression battery for the Tier-2 deterministic renderer
(agent.pipeline.build_chart). Complements run_eval (which scores NL→SQL):
this scores DATA-SHAPE → CHART-SHAPE, deterministically and WITHOUT the LLM.

Why separate: the renderer's job is to map a result's shape to the right chart
(overlaid lines for levels, stacked area for compositions over time, bars for
breakdowns). That mapping must generalise across questions, so we pin it with
fixed SQL → fixed expected chart kind — no eyeball-debugging per question.

Run:  .venv/Scripts/python.exe -m eval.charts
"""
from __future__ import annotations

import plotly.graph_objects as go

from agent.pipeline import build_chart, render_spec, run_sql

# (id, expected_kind, sql). expected_kind ∈ {line, area, bar}.
# (id, expected_kind, title, sql). title is passed to build_chart, so it also
# exercises the chart-type INTENT (a title with "扇形图" must yield a pie).
CASES: list[tuple[str, str, str, str]] = [
    ("nav_single", "line", "易方达蓝筹精选今年单位净值走势",
     "SELECT n.nav_date, n.unit_nav FROM fact_nav n JOIN dim_fund f "
     "ON n.fund_code=f.fund_code WHERE f.fund_name LIKE '%易方达蓝筹精选%' "
     "AND n.nav_date>='2026-01-01' ORDER BY n.nav_date"),
    ("nav_compare", "line", "易方达蓝筹精选与华夏成长今年净值对比",
     "SELECT n.nav_date, f.fund_name, n.unit_nav FROM fact_nav n JOIN dim_fund f "
     "ON n.fund_code=f.fund_code WHERE (f.fund_name LIKE '%易方达蓝筹精选%' "
     "OR f.fund_name LIKE '%华夏成长%') AND n.nav_date>='2026-01-01' ORDER BY n.nav_date"),
    # the headline class this battery was created for: industry positions over
    # time → stacked area, one band per 申万 industry (was a garbage single line
    # before the renderer learned to infer the series dimension).
    ("industry_over_time", "area", "易方达蓝筹精选近年各申万行业仓位变化",
     "SELECT fh.report_date, ds.industry_l1, SUM(fh.weight) AS industry_weight "
     "FROM fact_holding fh JOIN dim_fund df ON fh.fund_code=df.fund_code "
     "JOIN dim_security ds ON fh.sec_code=ds.sec_code "
     "WHERE df.fund_name LIKE '%易方达蓝筹精选%' AND fh.report_date>='2022-01-01' "
     "AND fh.is_full_holding AND ds.industry_l1 IS NOT NULL "
     "GROUP BY fh.report_date, ds.industry_l1"),
    ("ranking_bar", "bar", "今年以来收益率最高的基金排名",
     "SELECT f.fund_name, p.total_return FROM v_fund_perf_latest p JOIN dim_fund f "
     "ON p.fund_code=f.fund_code WHERE p.period='YTD' ORDER BY p.total_return DESC LIMIT 10"),
    # chart-type INTENT: an explicit 扇形图 request must override the default bar.
    ("industry_pie", "pie", "易方达蓝筹精选最新一期行业配置（用扇形图展示）",
     "SELECT industry_l1, industry_weight FROM v_fund_industry_alloc "
     "WHERE fund_name LIKE '%易方达蓝筹精选%' AND is_latest ORDER BY industry_weight DESC"),
    # two metrics, no time axis → scatter (风险-收益), even without a keyword.
    ("risk_return_scatter", "scatter", "所有基金今年以来年化收益率与波动率",
     "SELECT fund_name, annual_return, volatility FROM v_fund_perf_latest WHERE period='YTD'"),
]


def chart_kind(fig: go.Figure) -> tuple[str, int]:
    """Classify a figure into {empty, bar, pie, area, scatter, line} + trace count."""
    if not fig.data:
        return "empty", 0
    if isinstance(fig.data[0], go.Bar):
        return "bar", len(fig.data)
    if isinstance(fig.data[0], go.Pie):
        return "pie", len(fig.data)
    # Scatter family: area (stacked fill) / scatter (markers) / line (lines).
    if any(getattr(tr, "stackgroup", None) for tr in fig.data):
        return "area", len(fig.data)
    mode = getattr(fig.data[0], "mode", "") or ""
    if "markers" in mode and "lines" not in mode:
        return "scatter", len(fig.data)
    return "line", len(fig.data)


# Tier-3 renderer (render_spec) — hand-written specs → expected kind, no LLM.
# Pins the declarative path incl. rich encodings (color/orientation/normalize).
# (id, expected_kind, sql, spec)
SPEC_CASES: list[tuple[str, str, str, dict]] = [
    ("spec_scatter_color", "scatter",
     "SELECT fund_name, annual_return, volatility, sharpe FROM v_fund_perf_latest "
     "WHERE period='YTD'",
     {"chart_type": "scatter", "x": "volatility", "y": "annual_return",
      "color": "sharpe", "title": "风险-收益（夏普着色）"}),
    ("spec_bar_horizontal", "bar",
     "SELECT industry_l1, industry_weight FROM v_fund_industry_alloc "
     "WHERE fund_name LIKE '%易方达蓝筹精选%' AND is_latest",
     {"chart_type": "bar", "x": "industry_l1", "y": "industry_weight",
      "orientation": "h", "sort": {"by": "industry_weight", "dir": "desc"},
      "title": "行业配置"}),
    ("spec_area_percent", "area",
     "SELECT report_date, industry_l1, industry_weight FROM v_fund_industry_alloc "
     "WHERE fund_name LIKE '%易方达蓝筹精选%' AND report_date>='2023-06-01'",
     {"chart_type": "area", "x": "report_date", "y": "industry_weight",
      "color": "industry_l1", "normalize": "percent", "title": "行业占比演变"}),
    # wide result (CR5/CR10 集中度) → y as a LIST → two series via melt.
    ("spec_multi_y", "line",
     "SELECT report_date, SUM(CASE WHEN holding_rank<=5 THEN weight ELSE 0 END) AS top5_weight, "
     "SUM(CASE WHEN holding_rank<=10 THEN weight ELSE 0 END) AS top10_weight "
     "FROM v_fund_top_holdings WHERE fund_name LIKE '%易方达蓝筹精选%' AND holding_rank<=10 "
     "GROUP BY report_date",
     {"chart_type": "line", "x": "report_date",
      "y": ["top5_weight", "top10_weight"], "title": "前五大/前十大集中度"}),
]


def main() -> int:
    passed = total = 0
    for cid, expected, title, sql in CASES:
        total += 1
        try:
            df = run_sql(sql)
            kind, n = chart_kind(build_chart(df, title))
            ok = kind == expected
            detail = f"kind={kind} traces={n} rows={len(df)}"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid:22s} expect={expected:7s} — {detail}")

    print("  --- Tier-3 render_spec ---")
    for cid, expected, sql, spec in SPEC_CASES:
        total += 1
        try:
            df = run_sql(sql)
            kind, n = chart_kind(render_spec(df, spec))
            ok = kind == expected
            detail = f"kind={kind} traces={n} rows={len(df)}"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid:22s} expect={expected:7s} — {detail}")

    print("\n" + "=" * 56)
    print(f"  CHARTS : {passed}/{total}")
    print("=" * 56)
    return 1 if passed < total else 0


if __name__ == "__main__":
    raise SystemExit(main())

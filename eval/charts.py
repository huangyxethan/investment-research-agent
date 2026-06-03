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

from agent.pipeline import build_chart, run_sql

# (id, expected_kind, sql). expected_kind ∈ {line, area, bar}.
CASES: list[tuple[str, str, str]] = [
    ("nav_single", "line",
     "SELECT n.nav_date, n.unit_nav FROM fact_nav n JOIN dim_fund f "
     "ON n.fund_code=f.fund_code WHERE f.fund_name LIKE '%易方达蓝筹精选%' "
     "AND n.nav_date>='2026-01-01' ORDER BY n.nav_date"),
    ("nav_compare", "line",
     "SELECT n.nav_date, f.fund_name, n.unit_nav FROM fact_nav n JOIN dim_fund f "
     "ON n.fund_code=f.fund_code WHERE (f.fund_name LIKE '%易方达蓝筹精选%' "
     "OR f.fund_name LIKE '%华夏成长%') AND n.nav_date>='2026-01-01' ORDER BY n.nav_date"),
    # the headline class this battery was created for: industry positions over
    # time → stacked area, one band per 申万 industry (was a garbage single line
    # before the renderer learned to infer the series dimension).
    ("industry_over_time", "area",
     "SELECT fh.report_date, ds.industry_l1, SUM(fh.weight) AS industry_weight "
     "FROM fact_holding fh JOIN dim_fund df ON fh.fund_code=df.fund_code "
     "JOIN dim_security ds ON fh.sec_code=ds.sec_code "
     "WHERE df.fund_name LIKE '%易方达蓝筹精选%' AND fh.report_date>='2022-01-01' "
     "AND fh.is_full_holding AND ds.industry_l1 IS NOT NULL "
     "GROUP BY fh.report_date, ds.industry_l1"),
    ("ranking_bar", "bar",
     "SELECT f.fund_name, p.total_return FROM v_fund_perf_latest p JOIN dim_fund f "
     "ON p.fund_code=f.fund_code WHERE p.period='YTD' ORDER BY p.total_return DESC LIMIT 10"),
]


def chart_kind(fig: go.Figure) -> tuple[str, int]:
    """Classify a figure into {empty, bar, area, line} + trace count."""
    if not fig.data:
        return "empty", 0
    if isinstance(fig.data[0], go.Bar):
        return "bar", len(fig.data)
    stacked = any(getattr(tr, "stackgroup", None) for tr in fig.data)
    return ("area" if stacked else "line"), len(fig.data)


def main() -> int:
    passed = 0
    for cid, expected, sql in CASES:
        try:
            df = run_sql(sql)
            kind, n = chart_kind(build_chart(df, cid))
            ok = kind == expected
            detail = f"kind={kind} traces={n} rows={len(df)}"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid:20s} expect={expected:5s} — {detail}")

    print("\n" + "=" * 56)
    print(f"  CHARTS : {passed}/{len(CASES)}")
    print("=" * 56)
    return 1 if passed < len(CASES) else 0


if __name__ == "__main__":
    raise SystemExit(main())

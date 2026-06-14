"""
Latency profiler (leader feedback B2: "返回速度多少 / 时间花在哪里").

Runs the FULL ask() pipeline over a small representative question set and prints a
per-stage breakdown so we can say, on stage, exactly where the ~seconds go. Stages
are grouped into LLM (sql_gen + narrate + chart_spec), DB (schema + db_exec) and
LOCAL (chart_render) — the headline answer to "慢在哪".

Run:  .venv/Scripts/python.exe -m eval.perf [--repeat N]
"""
from __future__ import annotations

import argparse
import statistics

from agent.pipeline import ask

# representative across chart types & 口径 (nav / metric / industry-L1 / industry-L2 /
# manager / cross-table) so the mix mirrors real usage.
QUESTIONS = [
    "易方达蓝筹精选今年的单位净值走势",
    "今年以来收益率排名前十的基金",
    "易方达蓝筹精选最新一期的行业配置分布",
    "重仓半导体行业最多的前三只基金",
    "葛兰目前管理哪些基金",
    "张坤管理的基金中今年以来夏普最高的那只，前五大重仓股是什么",
]

STAGES = ["schema", "sql_gen", "db_exec", "narrate", "chart_spec", "chart_render"]
GROUPS = {
    "LLM   (生成SQL+解读+图表spec)": ["sql_gen", "narrate", "chart_spec"],
    "DB    (读schema+执行查询)":     ["schema", "db_exec"],
    "LOCAL (图表渲染)":              ["chart_render"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="runs per question")
    args = ap.parse_args()

    runs: list[dict[str, float]] = []
    print(f"=== perf: {len(QUESTIONS)} questions x {args.repeat} ===\n")
    for q in QUESTIONS:
        for _ in range(args.repeat):
            t = ask(q).timings
            runs.append(t)
            print(f"  {t['total']:5.1f}s  {q[:34]:34s} "
                  f"(sql {t['sql_gen']:.1f} / db {t['db_exec']:.2f} / "
                  f"narr {t['narrate']:.1f} / spec {t['chart_spec']:.1f})")

    n = len(runs)
    avg = lambda k: sum(r.get(k, 0) for r in runs) / n  # noqa: E731
    totals = sorted(r["total"] for r in runs)
    grand = avg("total")

    print("\n" + "=" * 56)
    print(f"  {'stage':14s} {'avg(s)':>8s} {'% of total':>12s}")
    print("-" * 56)
    for s in STAGES:
        print(f"  {s:14s} {avg(s):>8.2f} {100*avg(s)/grand:>11.0f}%")
    print("-" * 56)
    for label, members in GROUPS.items():
        g = sum(avg(m) for m in members)
        print(f"  {label:28s} {g:>6.2f}s {100*g/grand:>5.0f}%")
    print("-" * 56)
    p50 = totals[len(totals) // 2]
    print(f"  TOTAL  avg {grand:.1f}s | p50 {p50:.1f}s | "
          f"min {totals[0]:.1f}s | max {totals[-1]:.1f}s")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

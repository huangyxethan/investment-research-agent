"""
Multi-dimensional STRESS runner.

Drives the REAL agent pipeline (generate_sql -> sql_guard -> run_sql) over a bank
of hard, multi-table-join questions tagged by scope dimension, then classifies each
outcome and reports by dimension so we can SEE which capability is weak and feed
targeted, semantic-layer fixes (not prompt patches).

Outcome classes per question:
  OK         executed, non-empty result
  EMPTY      executed but 0 rows (likely wrong routing / over-strict filter)
  EXEC_FAIL  SQL generated but errored at the DB (hallucinated col / wrong view /
             broken join) — the multi-table instability this battery exists to probe
  GEN_FAIL   no SQL produced or sql_guard rejected it
  VALUE_FAIL executed & non-empty but an `expect` substring was missing

Also measures, per question: actual join depth (distinct tables/views in the SQL,
via sqlglot) vs the expected min_tables, and LLM+DB latency (doubles as a latency probe).

Run:  python -m eval.stress
"""
from __future__ import annotations

import pathlib
import time
from collections import defaultdict

import sqlglot
import yaml
from sqlglot import exp

from agent.clients import default_client
from agent.pipeline import generate_sql, run_sql, schema_context

BANK = pathlib.Path(__file__).parent / "stress_questions.yaml"


def table_count(sql: str) -> int:
    """Distinct base tables/views referenced (join-depth proxy)."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
        return len({t.name for t in tree.find_all(exp.Table) if t.name})
    except Exception:
        return -1


def classify(q: dict, llm, schema) -> dict:
    cid = q["id"]
    t0 = time.perf_counter()
    sql, gen_dt, exec_dt, ntab = "(none)", 0.0, 0.0, -1
    try:
        sql = generate_sql(q["question"], llm, schema)
        gen_dt = time.perf_counter() - t0
        ntab = table_count(sql)
    except Exception as e:
        return dict(id=cid, outcome="GEN_FAIL", detail=f"{type(e).__name__}: {e}",
                    sql=sql, gen_dt=time.perf_counter() - t0, exec_dt=0.0, ntab=-1)

    t1 = time.perf_counter()
    try:
        df = run_sql(sql)
    except Exception as e:
        return dict(id=cid, outcome="EXEC_FAIL", detail=f"{type(e).__name__}: {e}",
                    sql=sql, gen_dt=gen_dt, exec_dt=time.perf_counter() - t1, ntab=ntab)
    exec_dt = time.perf_counter() - t1

    if len(df) == 0:
        outcome, detail = "EMPTY", "0 rows"
    elif q.get("expect") and not all(str(v) in df.to_string() for v in q["expect"]):
        outcome, detail = "VALUE_FAIL", f"missing {q['expect']} in {len(df)} rows"
    else:
        outcome, detail = "OK", f"{len(df)} rows"
    return dict(id=cid, outcome=outcome, detail=detail, sql=sql,
                gen_dt=gen_dt, exec_dt=exec_dt, ntab=ntab)


def main() -> int:
    bank = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    qby = {q["id"]: q for q in bank}
    llm = default_client()
    schema = schema_context()

    rows = []
    print(f"=== stress: {len(bank)} questions across "
          f"{len(set(q['dim'] for q in bank))} dimensions ===\n")
    for q in bank:
        r = classify(q, llm, schema)
        r["dim"], r["min_tables"] = q["dim"], q.get("min_tables", 0)
        shallow = "" if r["ntab"] < 0 or r["ntab"] >= r["min_tables"] else " ⚠shallow"
        print(f"  [{r['outcome']:9s}] {r['id']:16s} ({r['dim']:8s}) "
              f"tables={r['ntab']}/{r['min_tables']}{shallow} "
              f"{r['gen_dt']+r['exec_dt']:.1f}s — {r['detail']}")
        rows.append(r)

    # --- scorecard by dimension ---
    by_dim: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_dim[r["dim"]].append(r)
    print("\n" + "=" * 64)
    print(f"  {'dim':9s} {'OK':>3s} {'EMPTY':>6s} {'EXEC':>5s} {'GEN':>4s} {'VAL':>4s}  {'avg_tbl':>7s}")
    print("-" * 64)
    for dim in sorted(by_dim):
        v = by_dim[dim]
        cnt = lambda o: sum(1 for r in v if r["outcome"] == o)  # noqa: E731
        tabs = [r["ntab"] for r in v if r["ntab"] >= 0]
        print(f"  {dim:9s} {cnt('OK'):>3d} {cnt('EMPTY'):>6d} {cnt('EXEC_FAIL'):>5d} "
              f"{cnt('GEN_FAIL'):>4d} {cnt('VALUE_FAIL'):>4d}  "
              f"{(sum(tabs)/len(tabs) if tabs else 0):>7.1f}")
    print("-" * 64)
    ok = sum(1 for r in rows if r["outcome"] == "OK")
    lat = [r["gen_dt"] + r["exec_dt"] for r in rows]
    print(f"  TOTAL OK : {ok}/{len(rows)}  ({100*ok//len(rows)}%)   "
          f"latency avg {sum(lat)/len(lat):.1f}s / max {max(lat):.1f}s")
    print("=" * 64)

    bad = [r for r in rows if r["outcome"] not in ("OK",)]
    if bad:
        print("\n--- NON-OK (generated SQL) ---")
        for r in bad:
            print(f"\n• [{r['outcome']}] {r['id']} ({r['dim']}): {qby[r['id']]['question']}"
                  f"\n  → {r['detail']}\n  {r['sql']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

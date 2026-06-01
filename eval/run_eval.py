"""
Run the NL2SQL eval set and print a scorecard by capability class.

Scores SQL generation + execution only (no narrative/chart) to stay cheap and
focused. Failures print their generated SQL so we can see PATTERNS to fix in the
semantic layer — not one-off prompt patches.

Run:  .venv/Scripts/python.exe -m eval.run_eval
"""
from __future__ import annotations

import pathlib
from collections import defaultdict

import yaml

from agent.clients import default_client
from agent.pipeline import generate_sql, run_sql, schema_context

CASES = pathlib.Path(__file__).parent / "cases.yaml"


def check(mode: str, expect, df) -> bool:
    if mode == "nonempty":
        return len(df) > 0
    if mode == "rowcount":
        return len(df) == int(expect)
    if mode == "values":
        blob = df.to_string()
        return all(str(v) in blob for v in (expect or []))
    raise ValueError(f"unknown mode {mode}")


def main() -> int:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    llm = default_client()
    schema = schema_context()

    by_class: dict[str, list[bool]] = defaultdict(list)
    failures = []
    for c in cases:
        cid, cls, q = c["id"], c["class"], c["question"]
        sql = "(not generated)"
        try:
            sql = generate_sql(q, llm, schema)
            df = run_sql(sql)
            ok = check(c["mode"], c.get("expect"), df)
            detail = f"{len(df)} rows"
        except Exception as e:
            ok, sql, detail = False, locals().get("sql", "(no SQL)"), f"{type(e).__name__}: {e}"
        by_class[cls].append(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {cid:14s} ({cls:7s}) — {detail}")
        if not ok:
            failures.append((cid, q, sql, detail))

    print("\n" + "=" * 60)
    total_ok = sum(v.count(True) for v in by_class.values())
    total = sum(len(v) for v in by_class.values())
    for cls in sorted(by_class):
        v = by_class[cls]
        print(f"  {cls:8s}: {v.count(True)}/{len(v)}")
    print("-" * 60)
    print(f"  TOTAL   : {total_ok}/{total}  ({100*total_ok//total}%)")
    print("=" * 60)

    if failures:
        print("\n--- FAILURES (generated SQL) ---")
        for cid, q, sql, detail in failures:
            print(f"\n• {cid}: {q}\n  → {detail}\n  {sql}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Grow the (already share-class-deduped) universe back up to ~TARGET_TOTAL funds,
with ~50% E Fund (易方达), WITHOUT re-introducing share-class dupes.

- 易方达: company-filtered active equity funds from ak.fund_name_em(), deduped by
  base_name() and excluding bases already in the universe.
- others : performance-ranked 混合型/股票型 from ak.fund_open_fund_rank_em()
  (same source the original universe used), deduped + excluding existing bases.

Appends new rows to fund_universe.csv and writes the NEW codes to
etl/_new_codes.txt so the data pull can run delta-only (UNIVERSE_ONLY).

Run:  python -m etl.expand_universe
"""
from __future__ import annotations

import csv
import pathlib

import akshare as ak
import pandas as pd

from etl.dedup_universe import base_name
from etl.universe import HERO

_CSV = pathlib.Path(__file__).parent / "fund_universe.csv"
_NEW = pathlib.Path(__file__).parent / "_new_codes.txt"

TARGET_TOTAL = 200
TARGET_YFD = 100          # ~50% 易方达
RANK_PULL = 200           # how many ranked funds to scan for the "others" top-up


def is_equity(fund_type: str) -> bool:
    """Active equity-ish only: exclude index / bond / money / QDII / FOF / Reits."""
    t = str(fund_type)
    if t.startswith("指数型") or "债" in t or "货币" in t or "QDII" in t \
            or "FOF" in t or "Reits" in t:
        return False
    return t.split("-")[0] in ("股票型", "混合型")


def _existing() -> tuple[list[dict], set[str], set[str], int]:
    rows = []
    if _CSV.exists():
        with _CSV.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    codes = {(r.get("fund_code") or "").strip() for r in rows}
    bases = {base_name(r.get("fund_name") or "") for r in rows}
    # hero labels carry "(经理)" suffixes — base their plain part too
    yfd = sum(1 for r in rows if (r.get("fund_name") or "").startswith("易方达"))
    return rows, codes, bases, yfd


def _pick(pool: pd.DataFrame, need: int, taken_bases: set[str],
          taken_codes: set[str]) -> list[dict]:
    out: list[dict] = []
    for r in pool.itertuples(index=False):
        if len(out) >= need:
            break
        code, name, ftype = str(r.fund_code), str(r.fund_name), str(r.fund_type)
        b = base_name(name)
        if code in taken_codes or b in taken_bases:
            continue
        taken_codes.add(code)
        taken_bases.add(b)
        out.append({"fund_code": code, "fund_name": name, "fund_type": ftype})
    return out


def main() -> int:
    rows, codes, bases, yfd_have = _existing()
    print(f"[existing] {len(rows)} funds ({yfd_have} 易方达, {len(rows)-yfd_have} 其他)")

    need_yfd = max(TARGET_YFD - yfd_have, 0)
    need_oth = max(TARGET_TOTAL - TARGET_YFD - (len(rows) - yfd_have), 0)
    print(f"[target]   +{need_yfd} 易方达, +{need_oth} 其他  -> ~{TARGET_TOTAL}")

    # --- 易方达 pool ---
    allf = ak.fund_name_em()
    yfd = allf[allf["基金简称"].astype(str).str.startswith("易方达")].copy()
    yfd = yfd[yfd["基金类型"].map(is_equity)]
    yfd = yfd.rename(columns={"基金代码": "fund_code", "基金简称": "fund_name"})
    yfd["fund_type"] = yfd["基金类型"].map(
        lambda t: "股票型" if str(t).split("-")[0] == "股票型" else "混合型")
    yfd_sel = _pick(yfd[["fund_code", "fund_name", "fund_type"]], need_yfd, bases, codes)
    print(f"[易方达]   selected {len(yfd_sel)} (pool {len(yfd)} share-class rows)")

    # --- others pool (ranked, recognizable) ---
    oth_sel: list[dict] = []
    for t in ("混合型", "股票型"):
        if len(oth_sel) >= need_oth:
            break
        try:
            d = ak.fund_open_fund_rank_em(symbol=t)
        except Exception as e:
            print(f"  [warn] rank {t}: {e}")
            continue
        cc = next(c for c in d.columns if "代码" in c)
        nc = next(c for c in d.columns if ("简称" in c or "名称" in c))
        d = d[[cc, nc]].head(RANK_PULL)
        d.columns = ["fund_code", "fund_name"]
        d["fund_type"] = t
        d = d[~d["fund_name"].astype(str).str.startswith("易方达")]
        oth_sel += _pick(d, need_oth - len(oth_sel), bases, codes)
    print(f"[其他]     selected {len(oth_sel)}")

    new = yfd_sel + oth_sel
    if not new:
        print("[done] nothing to add.")
        return 0

    fieldnames = ["fund_code", "fund_name", "fund_type"]
    with _CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in fieldnames} for r in rows)
        w.writerows(new)
    _NEW.write_text("\n".join(r["fund_code"] for r in new), encoding="utf-8")

    print(f"\n[done] added {len(new)} funds -> {_CSV.name} now {len(rows)+len(new)} total")
    print(f"[done] new codes -> {_NEW.name}")
    print("\n--- sample of new funds ---")
    for r in (yfd_sel[:6] + oth_sel[:4]):
        print(f"  {r['fund_code']} {r['fund_name']} [{r['fund_type']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

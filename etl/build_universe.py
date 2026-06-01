"""
Build/refresh the fund universe CSV: pull open-end 混合型 + 股票型 funds from
akshare, union with the curated HERO set, write etl/fund_universe.csv.

Volume over curation here — the point is scaling 12 -> 100+ funds of the same
data types. HERO funds stay guaranteed. Re-run to refresh the universe.

Run:  .venv/Scripts/python.exe -m etl.build_universe
"""
from __future__ import annotations

import pathlib
import re

import akshare as ak
import pandas as pd

from etl.universe import HERO

OUT = pathlib.Path(__file__).parent / "fund_universe.csv"
PER_TYPE = 70
_CODE = re.compile(r"^\d{6}$")


def pull(fund_type: str) -> pd.DataFrame:
    df = ak.fund_open_fund_rank_em(symbol=fund_type)
    code_col = next(c for c in df.columns if "代码" in c)
    name_col = next(c for c in df.columns if ("简称" in c or "名称" in c))
    out = df[[code_col, name_col]].copy()
    out.columns = ["fund_code", "fund_name"]
    out["fund_type"] = fund_type
    out = out[out["fund_code"].astype(str).str.match(_CODE)]
    return out.head(PER_TYPE)


def main() -> int:
    hero_df = pd.DataFrame(
        [{"fund_code": c, "fund_name": lbl, "fund_type": "hero"} for c, lbl in HERO.items()]
    )
    frames = [hero_df]
    for t in ("混合型", "股票型"):
        try:
            d = pull(t)
            print(f"  pulled {len(d)} {t}")
            frames.append(d)
        except Exception as e:
            print(f"  [warn] {t}: {e}")
    allf = pd.concat(frames).drop_duplicates("fund_code").reset_index(drop=True)
    allf.to_csv(OUT, index=False, encoding="utf-8")
    print(f"[done] wrote {len(allf)} funds -> {OUT}")
    print(allf.head(8).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

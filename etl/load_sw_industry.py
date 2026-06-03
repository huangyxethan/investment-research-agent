"""
W3+ step — populate dim_security.industry_l1 with 申万一级 (Shenwan L1) industry.

Source decision (see etl/explore_sw_industry*.py probes, retired):
  - ak.stock_industry_clf_hist_sw (official swsresearch.com xls) — SSL/read
    timeout from this network, unreliable.
  - ak.index_component_sw / index_hist_sw — JSONDecodeError in akshare 1.18.64.
  - legulegu index-composition — REACHABLE, one call per 申万 L1 code returns ALL
    constituents (not paginated) with a 申万1级 column. This is the source.
    (ak.sw_index_third_cons wraps the same host but hardcodes stale column names,
     so we call read_html ourselves.)

Coverage ~95% of held A-shares. Misses = delisted/suspended names from older
holding snapshots (absent from current 申万 constituent lists) + HK stocks
(申万 is A-share only). Left NULL by design — an honest data gap.

Cache: etl/sw_industry_l1.csv (committed snapshot for reproducibility / offline
ETL). Use --refresh to re-fetch from legulegu.

ETL order: ... -> load_holdings (creates dim_security rows) -> load_sw_industry.

Run:  .venv/Scripts/python.exe etl/load_sw_industry.py [--refresh]
"""
from __future__ import annotations

import os
import sys
import time
from io import StringIO

import akshare as ak
import pandas as pd
import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

CACHE = os.path.join(os.path.dirname(__file__), "sw_industry_l1.csv")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch_l1_constituents(code: str) -> pd.DataFrame:
    """All constituents of one 申万 L1 index, with their 申万1级 name."""
    url = f"https://legulegu.com/stockdata/index-composition?industryCode={code}"
    r = requests.get(url, headers=_UA, timeout=60)
    r.raise_for_status()
    df = pd.read_html(StringIO(r.text))[0]
    return df[["股票代码", "股票简称", "申万1级"]].copy()


def fetch_sw_l1_map() -> pd.DataFrame:
    """stock -> 申万一级 over all 31 L1 industries. Columns: code6, sec_name, sw_l1."""
    l1 = ak.sw_index_first_info()  # 行业代码, 行业名称, ...
    frames = []
    for row in l1.itertuples(index=False):
        code, name = str(row.行业代码), str(row.行业名称)
        try:
            frames.append(_fetch_l1_constituents(code))
            print(f"  ✓ {code} {name}: {len(frames[-1])} stocks")
        except Exception as e:
            print(f"  ✗ {code} {name}: {type(e).__name__}: {str(e)[:80]}")
        time.sleep(0.4)
    m = pd.concat(frames, ignore_index=True)
    m["code6"] = m["股票代码"].astype(str).str.split(".").str[0].str.zfill(6)
    m = m.rename(columns={"股票简称": "sec_name", "申万1级": "sw_l1"})
    return m[["code6", "sec_name", "sw_l1"]].drop_duplicates(subset="code6")


def load_map(refresh: bool = False) -> pd.DataFrame:
    if refresh or not os.path.exists(CACHE):
        print("[fetch] building 申万一级 map from legulegu ...")
        m = fetch_sw_l1_map()
        m.to_csv(CACHE, index=False, encoding="utf-8-sig")
        print(f"[cache] wrote {len(m)} rows -> {CACHE}")
    else:
        m = pd.read_csv(CACHE, dtype={"code6": str})
        m["code6"] = m["code6"].str.zfill(6)
        print(f"[cache] loaded {len(m)} rows from {CACHE}")
    return m


def main() -> int:
    refresh = "--refresh" in sys.argv
    m = load_map(refresh)
    code_to_l1 = dict(zip(m["code6"], m["sw_l1"]))

    with psycopg.connect(os.environ["PG_CONN_STR"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT sec_code FROM dim_security WHERE sec_type='stock';")
        held = [r[0] for r in cur.fetchall()]
        updated = 0
        for sec_code in held:
            l1 = code_to_l1.get(str(sec_code).split(".")[0].zfill(6))
            if l1:
                cur.execute(
                    "UPDATE dim_security SET industry_l1=%s "
                    "WHERE workspace_id='default' AND sec_code=%s;",
                    (l1, sec_code),
                )
                updated += 1
        conn.commit()

        cur.execute(
            "SELECT count(*) FILTER (WHERE industry_l1 IS NOT NULL), count(*) "
            "FROM dim_security WHERE sec_type='stock';"
        )
        have, total = cur.fetchone()
    print(f"\n[done] updated {updated} rows; industry_l1 set on {have}/{total} "
          f"stocks ({100*have/max(total,1):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

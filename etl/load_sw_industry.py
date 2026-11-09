"""
ETL step 4 — populate dim_security.industry_l1 / industry_l2 with 申万 (Shenwan)
一级/二级 industry classification.

Source decision (see retired etl/explore_sw_industry* probes):
  - ak.stock_industry_clf_hist_sw (official swsresearch.com xls) — SSL/read
    timeout from this network, unreliable.
  - ak.index_component_sw / index_hist_sw — JSONDecodeError in akshare 1.18.64.
  - legulegu index-composition — REACHABLE: one call per 申万 industry CODE returns
    ALL its constituents (not paginated). The page's level column varies (申万1级 for
    an L1 code, 申万2级 for an L2 code), so we DON'T read it — we take the industry
    name from the code we queried (sw_index_first_info / sw_index_second_info).
    (ak.sw_index_third_cons wraps the same host but hardcodes stale columns.)

Hierarchy: 31 L1 codes, 131 L2 codes; every A-share sits in exactly one L2, whose
parent L1 is sw_index_second_info.上级行业. L1 and L2 are fetched independently
(L1 already verified at 95.3%); coverage is the same source/universe.

Coverage ~95% of held A-shares. Misses = delisted/suspended names from older
holding snapshots + HK (港股通) + 北交所 (920xxx) — 申万 is A-share main/创业/科创
only. Left NULL by design — an honest data gap.

Caches: etl/sw_industry_l1.csv, etl/sw_industry_l2.csv (committed snapshots for
reproducibility / offline ETL). Use --refresh to re-fetch both from legulegu.

ETL order: ... -> load_holdings (creates dim_security rows) -> load_sw_industry.

Run:  python etl/load_sw_industry.py [--refresh]
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

_DIR = os.path.dirname(__file__)
CACHE_L1 = os.path.join(_DIR, "sw_industry_l1.csv")
CACHE_L2 = os.path.join(_DIR, "sw_industry_l2.csv")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch_constituents(code: str) -> pd.DataFrame:
    """All constituents (code6, sec_name) of one 申万 industry index code."""
    url = f"https://legulegu.com/stockdata/index-composition?industryCode={code}"
    r = requests.get(url, headers=_UA, timeout=60)
    r.raise_for_status()
    df = pd.read_html(StringIO(r.text))[0]
    out = pd.DataFrame({
        "code6": df["股票代码"].astype(str).str.split(".").str[0].str.zfill(6),
        "sec_name": df["股票简称"].astype(str),
    })
    return out


def fetch_sw_l1_map() -> pd.DataFrame:
    """stock -> 申万一级 over all 31 L1 industries. Columns: code6, sec_name, sw_l1."""
    l1 = ak.sw_index_first_info()  # 行业代码, 行业名称, ...
    frames = []
    for row in l1.itertuples(index=False):
        code, name = str(row.行业代码), str(row.行业名称)
        try:
            c = _fetch_constituents(code)
            c["sw_l1"] = name
            frames.append(c)
            print(f"  ✓ L1 {code} {name}: {len(c)} stocks")
        except Exception as e:
            print(f"  ✗ L1 {code} {name}: {type(e).__name__}: {str(e)[:80]}")
        time.sleep(0.4)
    m = pd.concat(frames, ignore_index=True)
    return m.drop_duplicates(subset="code6")[["code6", "sec_name", "sw_l1"]]


def fetch_sw_l2_map() -> pd.DataFrame:
    """stock -> 申万二级 (+ parent 一级) over all 131 L2 industries.
    Columns: code6, sec_name, sw_l2, sw_l1."""
    l2 = ak.sw_index_second_info()  # 行业代码, 行业名称, 上级行业, ...
    frames = []
    for row in l2.itertuples(index=False):
        code, name, parent = str(row.行业代码), str(row.行业名称), str(row.上级行业)
        try:
            c = _fetch_constituents(code)
            c["sw_l2"], c["sw_l1"] = name, parent
            frames.append(c)
            print(f"  ✓ L2 {code} {name} (<{parent}): {len(c)} stocks")
        except Exception as e:
            print(f"  ✗ L2 {code} {name}: {type(e).__name__}: {str(e)[:80]}")
        time.sleep(0.4)
    m = pd.concat(frames, ignore_index=True)
    return m.drop_duplicates(subset="code6")[["code6", "sec_name", "sw_l2", "sw_l1"]]


def load_cached(path: str, fetch_fn, label: str, refresh: bool) -> pd.DataFrame:
    if refresh or not os.path.exists(path):
        print(f"[fetch] building {label} map from legulegu ...")
        m = fetch_fn()
        m.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[cache] wrote {len(m)} rows -> {path}")
    else:
        m = pd.read_csv(path, dtype={"code6": str})
        m["code6"] = m["code6"].str.zfill(6)
        print(f"[cache] loaded {len(m)} rows from {path}")
    return m


def main() -> int:
    refresh = "--refresh" in sys.argv
    m1 = load_cached(CACHE_L1, fetch_sw_l1_map, "申万一级", refresh)
    m2 = load_cached(CACHE_L2, fetch_sw_l2_map, "申万二级", refresh)
    to_l1 = dict(zip(m1["code6"], m1["sw_l1"]))
    to_l2 = dict(zip(m2["code6"], m2["sw_l2"]))

    with psycopg.connect(os.environ["PG_CONN_STR"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT sec_code FROM dim_security WHERE sec_type='stock';")
        held = [r[0] for r in cur.fetchall()]
        n1 = n2 = 0
        for sec_code in held:
            k = str(sec_code).split(".")[0].zfill(6)
            l1, l2 = to_l1.get(k), to_l2.get(k)
            if l1 or l2:
                cur.execute(
                    "UPDATE dim_security SET industry_l1=COALESCE(%s, industry_l1), "
                    "industry_l2=COALESCE(%s, industry_l2) "
                    "WHERE workspace_id='default' AND sec_code=%s;",
                    (l1, l2, sec_code),
                )
                n1 += bool(l1)
                n2 += bool(l2)
        conn.commit()

        cur.execute(
            "SELECT count(*) FILTER (WHERE industry_l1 IS NOT NULL), "
            "       count(*) FILTER (WHERE industry_l2 IS NOT NULL), count(*) "
            "FROM dim_security WHERE sec_type='stock';"
        )
        h1, h2, total = cur.fetchone()
    print(f"\n[done] L1 set on {h1}/{total} ({100*h1/max(total,1):.1f}%); "
          f"L2 set on {h2}/{total} ({100*h2/max(total,1):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

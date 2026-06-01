"""
W3 step 1 — dim_fund enrichment + NAV (unit + accumulated) for the universe.

Supersedes the W2 load_nav.py (3 funds, unit-only). Pulls real metadata
(inception / type) from 雪球 basic-info and both NAV series from 东财.
Robust per-fund: a bad code warns and is skipped, never crashes the batch.

Run:  .venv/Scripts/python.exe etl/load_funds.py
"""
from __future__ import annotations

import datetime as dt
import os

import akshare as ak
import pandas as pd
import psycopg
from dotenv import load_dotenv

from etl.universe import CODES, UNIVERSE

load_dotenv()


def derive_type(name: str) -> str:
    if "指数" in name or "ETF" in name:
        return "指数型"
    if "债" in name:
        return "债券型"
    if "股票" in name:
        return "股票型"
    if "QDII" in name:
        return "QDII"
    return "混合型"


def basic_info(code: str) -> dict[str, str]:
    df = ak.fund_individual_basic_info_xq(symbol=code)
    return dict(zip(df["item"], df["value"]))


def fetch_nav(code: str) -> pd.DataFrame:
    unit = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    acc = ak.fund_open_fund_info_em(symbol=code, indicator="累计净值走势")
    m = unit.merge(acc, on="净值日期", how="left")
    out = pd.DataFrame(
        {
            "nav_date": pd.to_datetime(m["净值日期"]).dt.date,
            "unit_nav": pd.to_numeric(m["单位净值"], errors="coerce"),
            "acc_nav": pd.to_numeric(m["累计净值"], errors="coerce"),
            "daily_return": pd.to_numeric(m["日增长率"], errors="coerce") / 100.0,
        }
    )
    return out.dropna(subset=["unit_nav"])


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    ok, total = 0, 0
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        for code in CODES:
            try:
                info = basic_info(code)
                name = info.get("基金名称") or UNIVERSE[code]
                ftype = derive_type(info.get("基金全称", name))
                inception = None
                if info.get("成立时间"):
                    try:
                        inception = dt.date.fromisoformat(info["成立时间"][:10])
                    except ValueError:
                        pass
                cur.execute(
                    """INSERT INTO dim_fund (fund_code, fund_name, fund_type, inception_date)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (workspace_id, fund_code) DO UPDATE
                         SET fund_name = EXCLUDED.fund_name,
                             fund_type = EXCLUDED.fund_type,
                             inception_date = COALESCE(EXCLUDED.inception_date, dim_fund.inception_date);""",
                    (code, name, ftype, inception),
                )

                df = fetch_nav(code)
                rows = [
                    (code, r.nav_date, r.unit_nav, r.acc_nav, r.daily_return)
                    for r in df.itertuples(index=False)
                ]
                cur.executemany(
                    """INSERT INTO fact_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (workspace_id, fund_code, nav_date) DO UPDATE
                         SET unit_nav = EXCLUDED.unit_nav,
                             acc_nav = EXCLUDED.acc_nav,
                             daily_return = EXCLUDED.daily_return;""",
                    rows,
                )
                conn.commit()
                print(f"  ✓ {code} {name} [{ftype}] inception={inception} nav_rows={len(rows)}")
                ok += 1
                total += len(rows)
            except Exception as e:
                conn.rollback()
                print(f"  ✗ {code} {UNIVERSE.get(code,'')}: {type(e).__name__}: {e}")
    print(f"\n[done] {ok}/{len(CODES)} funds, {total} NAV rows")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Minimal NAV ETL (walking skeleton).

Loads a few funds' unit-NAV history from akshare into dim_fund + fact_nav.
Deliberately tiny; the full ETL lives in load_funds.py and its siblings. Purpose
here: prove the akshare -> transform -> Postgres path
end-to-end, including the 日增长率 ÷100 conversion and partition routing.

Run:  python etl/load_nav.py
"""
from __future__ import annotations

import os

import akshare as ak
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

# code -> (name, type). Hardcoded for the skeleton; load_funds.py pulls these from akshare.
FUNDS = {
    "005827": ("易方达蓝筹精选", "混合型"),
    "110011": ("易方达优质精选", "混合型"),
    "000001": ("华夏成长", "混合型"),
}


def fetch_nav(code: str) -> pd.DataFrame:
    """akshare 单位净值走势 -> normalised (nav_date, unit_nav, daily_return)."""
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    out = pd.DataFrame(
        {
            "nav_date": pd.to_datetime(df["净值日期"]).dt.date,
            "unit_nav": pd.to_numeric(df["单位净值"], errors="coerce"),
            # 日增长率 is a PERCENT (e.g. 0.01 == +0.01%) -> store as decimal
            "daily_return": pd.to_numeric(df["日增长率"], errors="coerce") / 100.0,
        }
    )
    return out.dropna(subset=["unit_nav"])


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    total = 0
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        for code, (name, ftype) in FUNDS.items():
            cur.execute(
                """INSERT INTO dim_fund (fund_code, fund_name, fund_type)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (workspace_id, fund_code) DO NOTHING;""",
                (code, name, ftype),
            )
            df = fetch_nav(code)
            rows = [
                (code, r.nav_date, r.unit_nav, r.daily_return)
                for r in df.itertuples(index=False)
            ]
            cur.executemany(
                """INSERT INTO fact_nav (fund_code, nav_date, unit_nav, daily_return)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (workspace_id, fund_code, nav_date) DO NOTHING;""",
                rows,
            )
            print(f"  {code} {name}: {len(rows)} NAV rows")
            total += len(rows)
        # Backfill inception_date from earliest NAV (proxy; load_funds.py pulls the real value).
        cur.execute(
            """UPDATE dim_fund d
               SET inception_date = s.mn
               FROM (SELECT fund_code, min(nav_date) AS mn
                     FROM fact_nav GROUP BY fund_code) s
               WHERE d.fund_code = s.fund_code AND d.inception_date IS NULL;"""
        )
        conn.commit()
    print(f"[done] upserted {total} NAV rows across {len(FUNDS)} funds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

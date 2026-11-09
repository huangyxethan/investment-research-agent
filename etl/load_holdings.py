"""
ETL step 3 — fact_holding + dim_security (the Brinson input).

Source: ak.fund_portfolio_hold_em(symbol, date=<year>) returns all of a year's
disclosed equity holdings; the 季度 string tags each report. Mapping:
  1季度 -> 03-31 (top-10)   2季度/中报 -> 06-30 (FULL)
  3季度 -> 09-30 (top-10)   4季度/年报 -> 12-31 (FULL)
is_full_holding = quarter in {2, 4}; our Brinson convention uses only the full ones.

Industry (申万) is left NULL here and filled by a later step. HK holdings
(5-digit codes like 00700) come through too; flag for industry coverage.

Run:  python etl/load_holdings.py
"""
from __future__ import annotations

import datetime as dt
import os
import re

import akshare as ak
import pandas as pd
import psycopg
from dotenv import load_dotenv

from etl.universe import CODES, UNIVERSE

load_dotenv()

YEARS = ["2022", "2023", "2024", "2025"]
_Q_RE = re.compile(r"(\d{4})年(\d)季度")
_Q_TO_MMDD = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def parse_report_date(quarter_str: str) -> tuple[dt.date | None, bool]:
    m = _Q_RE.search(str(quarter_str))
    if not m:
        return None, False
    year, q = int(m.group(1)), int(m.group(2))
    if q not in _Q_TO_MMDD:
        return None, False
    mm, dd = _Q_TO_MMDD[q]
    return dt.date(year, mm, dd), q in (2, 4)


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    sec_total, hold_total = 0, 0
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        for code in CODES:
            fund_rows = 0
            try:
              for year in YEARS:
                try:
                    df = ak.fund_portfolio_hold_em(symbol=code, date=year)
                except Exception:
                    continue
                if df is None or df.empty:
                    continue
                for r in df.itertuples(index=False):
                    sec_code = str(getattr(r, "股票代码")).strip()
                    sec_name = str(getattr(r, "股票名称")).strip()
                    report_date, is_full = parse_report_date(getattr(r, "季度"))
                    if not sec_code or report_date is None:
                        continue
                    weight = pd.to_numeric(getattr(r, "占净值比例"), errors="coerce")
                    weight = float(weight) / 100.0 if pd.notna(weight) else None
                    mv = pd.to_numeric(getattr(r, "持仓市值"), errors="coerce")
                    shares = pd.to_numeric(getattr(r, "持股数"), errors="coerce")

                    cur.execute(
                        """INSERT INTO dim_security (sec_code, sec_name, sec_type)
                           VALUES (%s, %s, 'stock')
                           ON CONFLICT (workspace_id, sec_code) DO UPDATE
                             SET sec_name = EXCLUDED.sec_name;""",
                        (sec_code, sec_name),
                    )
                    sec_total += 1
                    cur.execute(
                        """INSERT INTO fact_holding
                             (fund_code, report_date, sec_code, weight, market_value,
                              shares, is_full_holding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (workspace_id, fund_code, report_date, sec_code)
                           DO UPDATE SET weight = EXCLUDED.weight,
                                         market_value = EXCLUDED.market_value,
                                         shares = EXCLUDED.shares,
                                         is_full_holding = EXCLUDED.is_full_holding;""",
                        (
                            code, report_date, sec_code, weight,
                            float(mv) if pd.notna(mv) else None,
                            float(shares) if pd.notna(shares) else None,
                            is_full,
                        ),
                    )
                    fund_rows += 1
                    hold_total += 1
              conn.commit()
              print(f"  ✓ {code} {UNIVERSE[code]}: {fund_rows} holding rows")
            except Exception as e:
              conn.rollback()
              print(f"  ✗ {code} {UNIVERSE.get(code,'')}: {type(e).__name__}: {e}")
    print(f"\n[done] {hold_total} holding rows, {sec_total} dim_security upserts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

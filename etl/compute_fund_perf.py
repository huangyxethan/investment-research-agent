"""
W4/hot-path — pre-compute fact_fund_perf (blueprint §8): return / sharpe /
drawdown / volatility per fund per period, with ONE verified 口径.

This kills the "let the LLM derive metrics each time" failure class: the agent
answers metric questions with a trivial SELECT from this table instead of
reconstructing fragile math (which we showed gives 283.6% vs the true 60.1%).

Definitions (documented so they're defensible):
  * Returns use acc_nav (累计净值, dividend-adjusted), total = last/first - 1.
  * Daily returns r_t = acc_nav_t/acc_nav_{t-1} - 1 over the window.
  * annual_return = (1+total)^(252/n_trading_days) - 1.
  * volatility   = std(r)*sqrt(252).
  * sharpe       = mean(r)/std(r)*sqrt(252), rf = 0 (stated assumption).
  * max_drawdown = max peak-to-trough decline on acc_nav (stored positive).
  * Periods: YTD, 1Y, 3Y, SI (since inception). as_of = latest nav_date.

Run:  .venv/Scripts/python.exe -m etl.compute_fund_perf
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

TRADING_DAYS = 252


def window_start(asof: pd.Timestamp, period: str) -> pd.Timestamp | None:
    if period == "YTD":
        return pd.Timestamp(asof.year, 1, 1)
    if period == "1Y":
        return asof - pd.Timedelta(days=365)
    if period == "3Y":
        return asof - pd.Timedelta(days=365 * 3)
    if period == "SI":
        return None  # full history
    raise ValueError(period)


def metrics(s: pd.Series) -> dict:
    """s: acc_nav indexed by date, ascending, within the window."""
    s = s.dropna()
    if len(s) < 2:
        return {}
    total = s.iloc[-1] / s.iloc[0] - 1
    r = s.pct_change().dropna()
    n = len(r)
    years = n / TRADING_DAYS
    annual = (1 + total) ** (1 / years) - 1 if years > 0 else None
    vol = float(r.std() * np.sqrt(TRADING_DAYS)) if n > 1 else None
    sharpe = float(r.mean() / r.std() * np.sqrt(TRADING_DAYS)) if r.std() else None
    runmax = s.cummax()
    mdd = float((s / runmax - 1).min())  # negative
    return {
        "total_return": float(total),
        "annual_return": float(annual) if annual is not None else None,
        "sharpe": sharpe,
        "max_drawdown": abs(mdd),
        "volatility": vol,
    }


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute("SELECT fund_code, nav_date, acc_nav FROM fact_nav ORDER BY fund_code, nav_date;")
        df = pd.DataFrame(cur.fetchall(), columns=["fund_code", "nav_date", "acc_nav"])
        df["nav_date"] = pd.to_datetime(df["nav_date"])
        df["acc_nav"] = pd.to_numeric(df["acc_nav"], errors="coerce")

        rows = []
        for code, g in df.groupby("fund_code"):
            g = g.sort_values("nav_date").set_index("nav_date")
            asof = g.index.max()
            for period in ("YTD", "1Y", "3Y", "SI"):
                start = window_start(asof, period)
                w = g.loc[start:asof, "acc_nav"] if start is not None else g["acc_nav"]
                m = metrics(w)
                if m:
                    rows.append((code, period, asof.date(), m))

        for code, period, asof, m in rows:
            cur.execute(
                """INSERT INTO fact_fund_perf
                     (fund_code, period, as_of_date, total_return, annual_return,
                      sharpe, max_drawdown, volatility)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (workspace_id, fund_code, period, as_of_date)
                   DO UPDATE SET total_return=EXCLUDED.total_return,
                                 annual_return=EXCLUDED.annual_return,
                                 sharpe=EXCLUDED.sharpe,
                                 max_drawdown=EXCLUDED.max_drawdown,
                                 volatility=EXCLUDED.volatility;""",
                (code, period, asof, m["total_return"], m["annual_return"],
                 m["sharpe"], m["max_drawdown"], m["volatility"]),
            )
        conn.commit()
        print(f"[done] wrote {len(rows)} fact_fund_perf rows")

        # --- offline verification: 005827 since-inception ≈ 0.601 (truth we checked) ---
        cur.execute("SELECT total_return FROM fact_fund_perf WHERE fund_code='005827' AND period='SI';")
        si = float(cur.fetchone()[0])
        print(f"[verify] 005827 SI total_return = {si:.4f}  (expected ≈ 0.60)")
        assert 0.55 < si < 0.65, f"SI return {si} off — check 口径!"
        print("[verify] OK ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

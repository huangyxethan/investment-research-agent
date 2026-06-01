"""
W3 data-discovery probe. akshare endpoint/column names drift and have units
surprises (the NAV smoke test taught us), so before writing the ETL we surface
what's actually available for: holdings, fund basic info, manager, acc-NAV.

Run:  .venv/Scripts/python.exe etl/explore_akshare.py
"""
from __future__ import annotations

import traceback

import akshare as ak

CODE = "005827"  # 易方达蓝筹精选 (张坤)


def probe(label: str, fn):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    try:
        df = fn()
        try:
            print(f"shape={df.shape}  columns={list(df.columns)}")
            print(df.head(6).to_string(index=False))
        except AttributeError:
            print(repr(df)[:1500])
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        traceback.print_exc(limit=1)


# 1. Holdings — quarterly vs semi-annual. Does akshare return >10 rows for
#    半年报/年报 (full holdings, which our Brinson口径 needs)?
probe("holdings 2025 — fund_portfolio_hold_em(date=2025)",
      lambda: ak.fund_portfolio_hold_em(symbol=CODE, date="2025"))
probe("holdings 2024 — fund_portfolio_hold_em(date=2024)",
      lambda: ak.fund_portfolio_hold_em(symbol=CODE, date="2024"))

# 2. Fund basic info (type / inception / benchmark / company / manager)
if hasattr(ak, "fund_individual_basic_info_xq"):
    probe("basic info — fund_individual_basic_info_xq",
          lambda: ak.fund_individual_basic_info_xq(symbol=CODE))

# 3. Accumulated NAV (we only loaded unit NAV in W2)
probe("acc-nav — fund_open_fund_info_em(indicator=累计净值走势)",
      lambda: ak.fund_open_fund_info_em(symbol=CODE, indicator="累计净值走势"))

# 4. Manager listing (for dim_manager + bridge_fund_manager)
for name in ("fund_manager_em", "fund_manager"):
    if hasattr(ak, name):
        probe(f"managers — {name}()", lambda n=name: getattr(ak, n)())
        break

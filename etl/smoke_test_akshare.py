"""
akshare smoke test.

Goal: prove the open-source data path works and SURFACE THE REAL COLUMN
NAMES akshare returns (they are Chinese and drift between versions — the ETL
must map them, so we discover them now rather than later).

Run:  python etl/smoke_test_akshare.py
"""
from __future__ import annotations

import sys
import traceback

# A few representative equity funds (manager-famous, liquid):
SAMPLE_FUNDS = {
    "005827": "易方达蓝筹精选 (张坤)",
    "110011": "易方达优质精选 (张坤)",
    "000001": "华夏成长",
}


def main() -> int:
    try:
        import akshare as ak
        import pandas as pd  # noqa: F401
    except Exception:  # pragma: no cover
        print("[FAIL] could not import akshare/pandas — run `pip install -r requirements.txt`")
        traceback.print_exc()
        return 1

    print(f"[ok] akshare {getattr(ak, '__version__', '?')}")

    ok = 0
    for code, name in SAMPLE_FUNDS.items():
        print(f"\n=== {code}  {name} ===")
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            print(f"  rows={len(df)}  columns={list(df.columns)}")
            print(df.head(3).to_string(index=False))
            ok += 1
        except Exception:
            print(f"  [warn] NAV pull failed for {code}")
            traceback.print_exc()

    print(f"\n[summary] {ok}/{len(SAMPLE_FUNDS)} funds pulled successfully.")
    print("Note the Chinese column names above — they become the ETL source->fact_nav mapping.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

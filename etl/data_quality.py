"""
Data-quality checks. Run as a battery against the loaded
warehouse; print a scorecard. FAIL = integrity problem (must fix), WARN =
plausible outlier to eyeball, INFO = coverage stat. Exits non-zero on any FAIL.

This is the logic that will later become Dagster AssetChecks (one extractor =
one asset = its checks). For now it runs standalone, like the rest of the ETL.

Run:  python -m etl.data_quality
"""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

# (name, severity, violation_sql) — each SQL returns the OFFENDING rows.
CHECKS: list[tuple[str, str, str]] = [
    ("nav: unit_nav null/<=0", "FAIL",
     "SELECT fund_code,nav_date,unit_nav FROM fact_nav WHERE unit_nav IS NULL OR unit_nav<=0"),
    ("nav: |daily_return|>20%", "WARN",
     "SELECT fund_code,nav_date,daily_return FROM fact_nav WHERE abs(daily_return)>0.2 ORDER BY abs(daily_return) DESC"),
    ("nav: acc_nav<unit_nav", "WARN",
     "SELECT fund_code,nav_date,unit_nav,acc_nav FROM fact_nav WHERE acc_nav IS NOT NULL AND acc_nav<unit_nav-0.0001"),
    ("dim_fund: bad code format", "FAIL",
     "SELECT fund_code FROM dim_fund WHERE fund_code !~ '^[0-9]{6}$'"),
    ("dim_fund: null inception", "WARN",
     "SELECT fund_code,fund_name FROM dim_fund WHERE inception_date IS NULL"),
    ("holding: single weight>100%", "FAIL",
     "SELECT fund_code,report_date,sec_code,weight FROM fact_holding WHERE weight>1.0"),
    ("holding: snapshot weight-sum>1.05", "WARN",
     "SELECT fund_code,report_date,round(sum(weight),3) AS wsum FROM fact_holding GROUP BY fund_code,report_date HAVING sum(weight)>1.05 ORDER BY sum(weight) DESC"),
    ("holding: FULL snapshot <11 stocks", "WARN",
     "SELECT fund_code,report_date,count(*) AS n FROM fact_holding WHERE is_full_holding GROUP BY fund_code,report_date HAVING count(*)<11"),
    ("perf: max_drawdown out of [0,1]", "FAIL",
     "SELECT fund_code,period,max_drawdown FROM fact_fund_perf WHERE max_drawdown<0 OR max_drawdown>1"),
    ("perf: YTD extreme (>300% / <-80%)", "WARN",
     "SELECT p.fund_code,d.fund_name,round(p.total_return,3) AS ytd FROM fact_fund_perf p JOIN dim_fund d ON p.fund_code=d.fund_code WHERE p.period='YTD' AND (p.total_return>3 OR p.total_return<-0.8) ORDER BY p.total_return DESC"),
    ("coverage: funds w/o ANY holdings", "INFO",
     "SELECT fund_code FROM dim_fund WHERE fund_code NOT IN (SELECT DISTINCT fund_code FROM fact_holding)"),
    ("coverage: funds w/o FULL-holding snapshot", "WARN",
     "SELECT fund_code FROM dim_fund WHERE fund_code IN (SELECT fund_code FROM fact_holding) AND fund_code NOT IN (SELECT DISTINCT fund_code FROM fact_holding WHERE is_full_holding)"),
    # ~142 by design (HK/delisted absent from current 申万 constituents); a jump
    # toward all-stocks would mean load_sw_industry didn't run.
    ("coverage: held stocks w/o 申万 industry", "INFO",
     "SELECT s.sec_code,s.sec_name FROM dim_security s WHERE s.sec_type='stock' AND s.industry_l1 IS NULL AND s.sec_code IN (SELECT DISTINCT sec_code FROM fact_holding)"),
]


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    tally = {"FAIL": 0, "WARN": 0, "INFO": 0}
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        for name, sev, sql in CHECKS:
            cur.execute(f"SELECT count(*) FROM ({sql}) t;")
            n = cur.fetchone()[0]
            status = "ok" if n == 0 else sev
            print(f"  [{status:4s}] {name}: {n}")
            if n:
                tally[sev] += 1
                cur.execute(f"{sql} LIMIT 3;")
                cols = [d.name for d in cur.description]
                for row in cur.fetchall():
                    print("          " + ", ".join(f"{c}={v}" for c, v in zip(cols, row)))
    print("\n" + "=" * 56)
    print(f"  FAIL={tally['FAIL']}  WARN={tally['WARN']}  INFO={tally['INFO']}")
    print("=" * 56)
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

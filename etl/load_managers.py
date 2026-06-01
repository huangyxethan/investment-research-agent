"""
W3 step 2 — dim_manager + bridge_fund_manager (current managers of the universe).

Source: ak.fund_manager_em() — a current manager->fund mapping. It carries NO
manager id and NO per-fund tenure start, so:
  * manager_id is synthesised as "公司:姓名" (stable, collision-safe enough).
  * bridge.start_date uses the fund inception as a PROXY (real tenure history /
    SCD2 needs a better source — deferred). is_current = true for all rows here.

Run:  .venv/Scripts/python.exe etl/load_managers.py
"""
from __future__ import annotations

import os

import akshare as ak
import psycopg
from dotenv import load_dotenv

from etl.universe import CODES

load_dotenv()


def main() -> int:
    conn_str = os.environ["PG_CONN_STR"]
    print("fetching fund_manager_em() (all managers, ~20s)…")
    df = ak.fund_manager_em()
    sub = df[df["现任基金代码"].astype(str).isin(CODES)].copy()
    print(f"  {len(sub)} manager-fund rows match the {len(CODES)}-fund universe")

    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        # fund inception map for the start_date proxy
        cur.execute("SELECT fund_code, inception_date FROM dim_fund;")
        inception = {c: d for c, d in cur.fetchall()}

        managers, bridges = 0, 0
        for r in sub.itertuples(index=False):
            name = getattr(r, "姓名")
            company = getattr(r, "所属公司")
            code = str(getattr(r, "现任基金代码"))
            tenure_days = getattr(r, "累计从业时间", None)
            manager_id = f"{company}:{name}"

            cur.execute(
                """INSERT INTO dim_manager (manager_id, manager_name, bio)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (workspace_id, manager_id) DO UPDATE
                     SET manager_name = EXCLUDED.manager_name, bio = EXCLUDED.bio;""",
                (manager_id, name, f"{company} · 累计从业 {tenure_days} 天"),
            )
            managers += cur.rowcount

            start = inception.get(code)
            if start is not None:
                cur.execute(
                    """INSERT INTO bridge_fund_manager
                         (fund_code, manager_id, start_date, is_current)
                       VALUES (%s, %s, %s, true)
                       ON CONFLICT (workspace_id, fund_code, manager_id, start_date)
                       DO NOTHING;""",
                    (code, manager_id, start),
                )
                bridges += cur.rowcount
        conn.commit()
    print(f"[done] dim_manager upserts={managers}, bridge rows={bridges}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

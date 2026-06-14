"""
Deduplicate share classes of the same fund (A/C/E/I ...). 公募 funds list the
SAME underlying fund under several 份额 codes (e.g. 财通成长优选混合A 001480 /
财通成长优选混合C 021528). For research we want ONE row per fund.

There is no clean 'share class' field, so we group by a NORMALIZED base name
(strip the trailing latin class letter) and keep one survivor per group:
  1) a HERO fund if the group contains one (curated, never dropped)
  2) else the class whose name ends in 'A' (前端主份额, usually原始/规模最大)
  3) else the class with the LONGEST nav history (most fact_nav rows)
tie-break: more nav rows, then smaller fund_code.

`base_name()` and `is_share_class_dup()` are reusable so task-3 universe rebuild
can avoid re-introducing dupes.

Usage:
  .venv/Scripts/python.exe -m etl.dedup_universe --dry-run   # analysis only
  .venv/Scripts/python.exe -m etl.dedup_universe --apply      # prune DB + CSV
"""
from __future__ import annotations

import argparse
import collections
import os
import pathlib
import re

import psycopg
from dotenv import load_dotenv

from etl.universe import HERO

load_dotenv(override=True)

_CSV = pathlib.Path(__file__).parent / "fund_universe.csv"

# Tables that reference dim_fund (FK, no ON DELETE CASCADE) — delete child rows
# first, then dim_fund. Empty reserved tables are harmless no-ops.
_CHILD_TABLES = [
    "fact_nav",
    "fact_holding",
    "fact_fund_perf",
    "bridge_fund_manager",
    "dim_fee",
    "dim_fund_scale",
    "fact_brinson_attribution",
    "fact_factor_exposure",
    "fact_conv_bond_holding",
    "fact_futures_holding",
]

_TRAIL_LETTER = re.compile(r"[A-Za-z]$")


def base_name(name: str) -> str:
    """Normalize a fund 简称 to its share-class-agnostic base."""
    n = name.strip()
    n = re.sub(r"类$", "", n)          # rare '...A类'
    n = _TRAIL_LETTER.sub("", n)       # strip one trailing latin class letter
    return n.strip()


def _fetch_funds(cur) -> list[tuple]:
    cur.execute(
        """
        SELECT d.fund_code, d.fund_name, d.fund_type,
               (SELECT count(*) FROM fact_nav     n WHERE n.fund_code = d.fund_code) AS nav,
               (SELECT count(*) FROM fact_holding h WHERE h.fund_code = d.fund_code) AS hold
        FROM dim_fund d
        ORDER BY d.fund_name
        """
    )
    return cur.fetchall()


def _pick_survivor(members: list[tuple]) -> tuple:
    hero = [m for m in members if m[0] in HERO]
    if hero:
        return hero[0]
    a_share = [m for m in members if m[1].strip().endswith("A")]
    pool = a_share or members
    # most nav rows, then smallest code
    return sorted(pool, key=lambda m: (-m[3], m[0]))[0]


def analyze(cur) -> dict:
    funds = _fetch_funds(cur)
    groups: dict[str, list[tuple]] = collections.defaultdict(list)
    for row in funds:
        groups[base_name(row[1])].append(row)

    keep, remove, dup_groups = [], [], []
    for base, members in groups.items():
        survivor = _pick_survivor(members)
        keep.append(survivor)
        dropped = [m for m in members if m[0] != survivor[0]]
        if dropped:
            dup_groups.append((base, survivor, dropped))
            remove.extend(dropped)
    return {
        "total": len(funds),
        "groups": len(groups),
        "keep": keep,
        "remove": remove,
        "dup_groups": sorted(dup_groups, key=lambda g: -len(g[2])),
    }


def _print_report(rep: dict) -> None:
    print(f"[universe] loaded funds in DB : {rep['total']}")
    print(f"[universe] distinct base funds : {rep['groups']}")
    print(f"[dedup]    funds to KEEP       : {len(rep['keep'])}")
    print(f"[dedup]    funds to REMOVE     : {len(rep['remove'])}")
    print(f"[dedup]    share-class groups w/ dupes: {len(rep['dup_groups'])}")
    print()
    print("=== share-class groups (KEEP ✓ / drop ✗) ===")
    for base, survivor, dropped in rep["dup_groups"]:
        kc, kn, kt, knav, khold = survivor
        print(f"\n• {base}")
        print(f"    ✓ KEEP {kc} {kn}  [nav={knav}, hold={khold}]")
        for dc, dn, dt, dnav, dhold in dropped:
            print(f"    ✗ drop {dc} {dn}  [nav={dnav}, hold={dhold}]")


def apply(cur, conn, rep: dict) -> None:
    remove_codes = [m[0] for m in rep["remove"]]
    if not remove_codes:
        print("[apply] nothing to remove.")
        return
    for t in _CHILD_TABLES:
        cur.execute(f"DELETE FROM {t} WHERE fund_code = ANY(%s)", (remove_codes,))
        print(f"  deleted {cur.rowcount:>7} rows from {t}")
    cur.execute("DELETE FROM dim_fund WHERE fund_code = ANY(%s)", (remove_codes,))
    print(f"  deleted {cur.rowcount:>7} rows from dim_fund")
    conn.commit()
    print(f"[apply] committed. removed {len(remove_codes)} share-class dupes.")
    _rewrite_csv({m[0] for m in rep["keep"]})


def _rewrite_csv(keep_codes: set[str]) -> None:
    import csv

    if not _CSV.exists():
        return
    with _CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    kept = [r for r in rows if (r.get("fund_code") or "").strip() in keep_codes]
    with _CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fund_code", "fund_name", "fund_type"])
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in w.fieldnames} for r in kept)
    print(f"[apply] rewrote {_CSV.name}: {len(rows)} -> {len(kept)} rows")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="prune DB + CSV")
    ap.add_argument("--dry-run", action="store_true", help="analysis only (default)")
    args = ap.parse_args()

    with psycopg.connect(os.environ["PG_CONN_STR"]) as conn:
        with conn.cursor() as cur:
            rep = analyze(cur)
            _print_report(rep)
            if args.apply:
                print()
                apply(cur, conn, rep)
            else:
                print("\n[dry-run] no changes made. Re-run with --apply to prune.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

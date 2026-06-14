"""
Fund universe = curated HERO funds (famous managers, for hero-chart demos)
UNION an auto-pulled broader set persisted in fund_universe.csv (built by
etl/build_universe.py). All ETL reads CODES, so the universe scales by
regenerating the CSV — no code edits needed.
"""
from __future__ import annotations

import csv
import os
import pathlib

# Curated, style-diverse, famous-manager funds — guaranteed in the universe so
# the demos always have good subjects.
HERO = {
    "005827": "易方达蓝筹精选 (张坤·消费/价值)",
    "110011": "易方达优质精选 (张坤)",
    "110022": "易方达消费行业 (萧楠·消费)",
    "003095": "中欧医疗健康 (葛兰·医药)",
    "260108": "景顺长城新兴成长 (刘彦春·消费)",
    "163406": "兴全合润 (谢治宇·均衡)",
    "161005": "富国天惠精选成长 (朱少醒·成长)",
    "005669": "前海开源公用事业 (崔宸龙·新能源)",
    "519066": "汇添富蓝筹稳健 (劳杰男·价值)",
    "001714": "工银瑞信文体产业 (袁芳·成长)",
    "161725": "招商中证白酒 (侯昊·指数/白酒)",
    "000001": "华夏成长 (老牌混合)",
}

_CSV = pathlib.Path(__file__).parent / "fund_universe.csv"


def _load_universe() -> dict[str, str]:
    codes: dict[str, str] = dict(HERO)
    if _CSV.exists():
        with _CSV.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = (row.get("fund_code") or "").strip()
                if code:
                    codes.setdefault(code, (row.get("fund_name") or "").strip())
    return codes


UNIVERSE = _load_universe()

# Delta hook: when UNIVERSE_ONLY is set (comma-separated codes), ETL scripts that
# iterate CODES (load_funds / load_holdings) restrict to just those — lets us pull
# ONLY newly added funds instead of re-pulling the whole universe.
_ONLY = {c.strip() for c in os.environ.get("UNIVERSE_ONLY", "").split(",") if c.strip()}
if _ONLY:
    UNIVERSE = {c: n for c, n in UNIVERSE.items() if c in _ONLY}

CODES = list(UNIVERSE)

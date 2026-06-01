"""
W2 — DeepSeek connectivity smoke test.

Validates DEEPSEEK_API_KEY + the OpenAI-compatible base_url independently,
before adding Vanna's complexity on top.

Run:  .venv/Scripts/python.exe etl/smoke_test_deepseek.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("[FAIL] DEEPSEEK_API_KEY not set in .env")
        return 1

    client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
    model = os.environ.get("LLM_BULK", "deepseek-chat")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You generate Postgres SQL. Reply with SQL only."},
                {"role": "user", "content": "查询 fund_code 为 005827 的最新单位净值。表 fact_nav(fund_code, nav_date, unit_nav)。"},
            ],
            temperature=0,
            max_tokens=200,
        )
    except Exception as e:  # pragma: no cover
        print(f"[FAIL] DeepSeek call errored: {e}")
        return 1

    print(f"[ok] model={model}")
    print("--- response ---")
    print(resp.choices[0].message.content)
    u = resp.usage
    print(f"--- tokens: prompt={u.prompt_tokens} completion={u.completion_tokens} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())

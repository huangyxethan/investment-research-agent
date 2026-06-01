"""
LLM client interface + implementations (blueprint §11: LLMClient ABC seam).

V1 uses DeepSeek for dev/bulk. The ABC is the swap point for Anthropic
(primary/showcase, D7) and, later, a Vanna-backed RAG client — all behind the
same `complete()` surface so the pipeline never changes.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMClient(ABC):
    """Minimal chat surface. Implementations route to a concrete backend."""

    @abstractmethod
    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        ...


class DeepSeekClient(LLMClient):
    """OpenAI-compatible DeepSeek (D7 'bulk' tier)."""

    def __init__(self, model: str | None = None):
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY missing in .env")
        self.model = model or os.environ.get("LLM_BULK", "deepseek-chat")
        self._client = OpenAI(api_key=key, base_url="https://api.deepseek.com")

    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""


def default_client() -> LLMClient:
    """Factory — single place to switch the default backend later."""
    return DeepSeekClient()

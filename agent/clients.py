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


class AnthropicClient(LLMClient):
    """Claude via the Anthropic Messages API (D7 primary/showcase tier).

    Same `complete()` surface as DeepSeek — the Anthropic-specific shape
    (system as a top-level arg, content blocks in the response) is absorbed here
    so the pipeline never changes."""

    def __init__(self, model: str | None = None):
        import anthropic  # lazy: only needed when Claude is actually selected

        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY missing in env/secrets")
        # uses LLM_PRIMARY (see .env.example); override if your account lacks this id
        self.model = model or os.environ.get("LLM_PRIMARY", "claude-sonnet-4-6")
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def make_client(provider: str | None = None) -> LLMClient:
    """Pick a backend by name (or LLM_PROVIDER env). Lets callers mix backends —
    e.g. DeepSeek for bulk NL2SQL, Claude for chart-spec authoring."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "deepseek")).strip().lower()
    if provider in ("claude", "anthropic"):
        return AnthropicClient()
    return DeepSeekClient()


def default_client() -> LLMClient:
    """Factory — single place to switch the default backend (via LLM_PROVIDER)."""
    return make_client()

"""LLM providers for the advisor: Google Gemini (REST, the free tier works) or Anthropic Claude (SDK).

LLM_PROVIDER=gemini|anthropic picks one explicitly. Without it, the provider follows whichever key is set:
GEMINI_API_KEY first, then ANTHROPIC_API_KEY. With no key, the deterministic template advisor is used.
"""

from __future__ import annotations

import os

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")  # 2.5-flash free tier: 20 requests/day
GEMINI_URL = os.environ.get("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

# USD per 1M tokens (input, output). Anthropic list prices Nov 2026; Gemini free tier = 0.
PRICING = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}

_gemini_http = None
_client = None


def provider() -> str | None:
    if os.environ.get("LLM_DISABLED") == "1":
        return None
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit in ("gemini", "google"):
        return "gemini" if os.environ.get("GEMINI_API_KEY") else None
    if explicit in ("anthropic", "claude"):
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def model_name() -> str | None:
    return {"gemini": GEMINI_MODEL, "anthropic": CLAUDE_MODEL}.get(provider() or "")


def gemini_http():
    global _gemini_http
    if _gemini_http is None:
        import httpx

        _gemini_http = httpx.Client(timeout=180)
    return _gemini_http


def client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model.startswith("gemini"):
        return 0.0
    pin, pout = PRICING.get(model, PRICING["claude-opus-5-5"])
    return (input_tokens * pin + output_tokens * pout) / 1_000_000

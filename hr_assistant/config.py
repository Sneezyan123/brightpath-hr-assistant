"""Settings loaded from environment / .env. All model ids and paths come from here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROVIDER_DEFAULTS = {
    # Cheapest current Claude model ($0.10 / $0.50 per 1M tokens) for both roles.
    "anthropic": {"model": "claude-haiku-5-5", "judge": "claude-haiku-5-5", "fallbacks": "", "min_interval": 0.0},
    "openrouter": {"model": "google/gemma-4-31b-it:free", "judge": "nvidia/nemotron-3-ultra-550b-a55b:free",
                   "fallbacks": "nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-26b-a4b-it:free",
                   "min_interval": 3.2},  # free tier allows ~20 requests/minute
}


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    provider: str  # "anthropic" or "openrouter"
    api_key: str | None
    model: str
    fallback_models: list[str]
    judge_model: str
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    db_path: Path = ROOT / "data" / "hr.db"
    seed_path: Path = ROOT / "data" / "seed.json"
    policies_dir: Path = ROOT / "data" / "policies"
    cache_dir: Path = ROOT / ".cache" / "llm"
    trace_path: Path = ROOT / "logs" / "traces.jsonl"
    use_cache: bool = True
    min_interval_s: float = 0.0
    today_override: str | None = None
    retrieval_k: int = 4
    referral_min_score: float = 4.0  # BM25 gate, calibrated in eval (see REPORT §5)
    extra: dict = field(default_factory=dict)


def get_settings() -> Settings:
    """Build settings from environment variables (re-read on each call so tests can override).

    Provider: HR_PROVIDER, else "anthropic" when ANTHROPIC_API_KEY is set, else "openrouter".
    """
    provider = os.getenv("HR_PROVIDER") or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "openrouter")
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"HR_PROVIDER must be one of {sorted(PROVIDER_DEFAULTS)}, got {provider!r}")
    d = PROVIDER_DEFAULTS[provider]
    key_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENROUTER_API_KEY"
    return Settings(
        provider=provider,
        api_key=os.getenv(key_var) or None,
        model=os.getenv("HR_MODEL", d["model"]),
        fallback_models=_csv(os.getenv("HR_FALLBACK_MODELS", d["fallbacks"])),
        judge_model=os.getenv("HR_JUDGE_MODEL", d["judge"]),
        min_interval_s=d["min_interval"],
        use_cache=os.getenv("HR_LLM_CACHE", "1") != "0",
        today_override=os.getenv("HR_TODAY") or None,
        referral_min_score=float(os.getenv("HR_REFERRAL_MIN_SCORE", "4.0")),
    )


def today(settings: Settings | None = None) -> date:
    """Current date, or HR_TODAY (YYYY-MM-DD) when set, for reproducible demos and evals."""
    s = settings or get_settings()
    return date.fromisoformat(s.today_override) if s.today_override else date.today()

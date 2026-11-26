"""Settings, read from the environment. The API process only holds the least-privilege database URL; the admin URL is
used by the one-shot bootstrap step alone."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("RELAY_DATABASE_URL"))
    admin_database_url: str = field(default_factory=lambda: _env("RELAY_ADMIN_DATABASE_URL"))
    app_db_password: str = field(default_factory=lambda: _env("RELAY_APP_DB_PASSWORD"))
    redis_url: str = field(default_factory=lambda: _env("RELAY_REDIS_URL"))

    # rules  = deterministic rule-based stand-in only (no key, no spending)
    # replay = recorded real-model answers from the cassette; never calls the API (visitors can also pick "rules")
    # record = replay on a hit, call the API on a miss and append it to the cassette (spends money)
    # live   = call the API (spends money, capped per day)
    llm_mode: str = field(default_factory=lambda: _env("RELAY_LLM_MODE", "rules"))
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    cassette: str = field(default_factory=lambda: _env("RELAY_CASSETTE", str(ROOT.parent / "eval" / "cassette.jsonl")))
    triage_model: str = field(default_factory=lambda: _env("RELAY_TRIAGE_MODEL", "claude-haiku-5-5"))
    answer_model: str = field(default_factory=lambda: _env("RELAY_ANSWER_MODEL", "claude-sonnet-5-5"))
    summary_model: str = field(default_factory=lambda: _env("RELAY_SUMMARY_MODEL", "claude-haiku-5-5"))
    daily_spend_cap_usd: float = field(default_factory=lambda: float(_env("RELAY_DAILY_SPEND_CAP_USD", "0.25")))

    embed_model: str = field(default_factory=lambda: _env("RELAY_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    # Below this retrieval confidence the bot says it does not know. Both picked on eval/calibration.yaml
    # (scripts/calibrate.py). The rule-based stand-in cannot judge relevance, so it needs the strict one; a model can also
    # refuse (NO_ANSWER), so it only needs the floor that kept every answerable development question.
    confidence_threshold: float = field(default_factory=lambda: float(_env("RELAY_CONFIDENCE_THRESHOLD", "0.74")))
    model_confidence_floor: float = field(default_factory=lambda: float(_env("RELAY_MODEL_CONFIDENCE_FLOOR", "0.60")))
    retrieval_k: int = 6
    max_failed_answers: int = 2

    slack_webhook_url: str = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    slack_max_per_hour: int = field(default_factory=lambda: int(_env("RELAY_SLACK_MAX_PER_HOUR", "20")))

    admin_token: str = field(default_factory=lambda: _env("RELAY_ADMIN_TOKEN"))
    retention_hours: int = field(default_factory=lambda: int(_env("RELAY_RETENTION_HOURS", "24")))
    rate_limit_per_min: int = field(default_factory=lambda: int(_env("RELAY_RATE_LIMIT_PER_MIN", "20")))
    max_message_chars: int = 1000
    max_messages_per_conversation: int = 30

    @property
    def engines(self) -> list[str]:
        """Answer engines a conversation may use, default first."""
        if self.llm_mode == "live" or self.llm_mode == "record":
            return ["live", "rules"]
        if self.llm_mode == "replay" and Path(self.cassette).is_file():
            return ["recorded", "rules"]
        return ["rules"]

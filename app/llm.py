"""Model access. One interface (triage, stream an answer, summarise a handoff) with two implementations:

* ``RuleEngine``: a deterministic stand-in with no model at all. Free; it quotes help center sentences verbatim.
* ``ModelEngine``: Claude, with record/replay cassettes. In ``replay`` mode it never touches the network.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from . import prompts, rules
from .config import Settings
from .retrieval import Hit, terms

PRICING = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}  # USD per million tokens (input, output)


class LLMError(RuntimeError):
    pass


class ReplayMiss(LLMError):
    """Recorded-answer mode and this exact request was never recorded: no network call, nothing spent."""


class AllowanceExhausted(LLMError):
    """A live call was needed but today's spend cap (scope "global") or the visitor's limit (scope "visitor") is used up."""

    def __init__(self, scope: str):
        super().__init__("the daily spend cap has been reached" if scope == "global" else "this visitor's daily limit has been reached")
        self.scope = scope


@dataclass
class Usage:
    role: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    first_token_ms: float = 0.0
    cost_usd: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def cost_of(model: str, tokens_in: int, tokens_out: int) -> float:
    for key, (pin, pout) in PRICING.items():
        if model.startswith(key):
            return (tokens_in * pin + tokens_out * pout) / 1_000_000
    return 0.0


def utc_day() -> date:
    return datetime.now(timezone.utc).date()


class LiveGate:
    """One customer message's permission to call the live model: counted once per message, however many calls it makes."""

    def __init__(self, sessions, visitor: str, per_day: int):
        self.sessions, self.visitor, self.per_day = sessions, visitor, per_day
        self._allowed: bool | None = None
        self._lock = threading.Lock()  # triage and the drafted answer ask at the same time

    def check(self) -> None:
        if not self.visitor or self.per_day <= 0:
            return
        with self._lock:
            if self._allowed is None:
                self._allowed = self.sessions.day_hit(self.visitor, utc_day().isoformat()) <= self.per_day
            if not self._allowed:
                raise AllowanceExhausted("visitor")


class Budget:
    """Daily spend cap (UTC day), in memory. A cap of 0 allows no live call at all."""

    def __init__(self, cap_usd: float):
        self.cap, self._day, self._spent, self._lock = cap_usd, utc_day(), 0.0, threading.Lock()

    def _roll(self) -> None:
        if utc_day() != self._day:
            self._day, self._spent = utc_day(), 0.0

    def add(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += usd

    @property
    def spent(self) -> float:
        with self._lock:
            self._roll()
            return self._spent

    def exhausted(self) -> bool:
        return self.spent >= self.cap


class Cassette:
    def __init__(self, path: str):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        if self.path and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.entries[entry["key"]] = entry

    @staticmethod
    def key(role: str, model: str, system: str, user: str) -> str:
        return hashlib.sha256(json.dumps([role, model, system, user]).encode()).hexdigest()

    def add(self, entry: dict) -> None:
        if not self.path:
            return
        with self._lock:
            self.entries[entry["key"]] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry) + "\n")


def template_summary(transcript: list[dict], reason: str) -> dict:
    asked = [m["text"] for m in transcript if m["role"] == "customer"]
    first, last = (asked[0], asked[-1]) if asked else ("", "")
    summary = f"Escalated for {reason.replace('_', ' ')} after {len(asked)} customer message(s). First message: \"{first[:200]}\""
    if len(asked) > 1:
        summary += f" Latest: \"{last[:200]}\""
    return {"summary": summary, "suspected_intent": reason.replace("_", " "),
            "next_step": "Read the transcript and reply to the customer."}


def format_transcript(transcript: list[dict]) -> str:
    return "\n".join(f"{m['role'].title()}: {m['text']}" for m in transcript)


class RuleEngine:
    name = "rules"

    def triage(self, message: str) -> tuple[dict, Usage]:
        return rules.classify(message), Usage("triage", "rule-based")

    def stream_answer(self, question: str, hits: list[Hit], previous: str, usage: list[Usage]) -> Iterator[str]:
        """Extractive: up to 3 sentences from the best article, chosen by word overlap, quoted verbatim with a citation."""
        usage.append(Usage("answer", "rule-based"))
        if not hits:
            yield prompts.NO_ANSWER
            return
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", hits[0].text.replace("\n\n", " ")) if s.strip()]
        asked = set(terms(question))
        ranked = sorted(range(len(sentences)), key=lambda i: (-len(asked & set(terms(sentences[i]))), i))[:3]
        for i in sorted(ranked):
            yield f"{sentences[i][:-1].rstrip()} [1]{sentences[i][-1]} "

    def summarize(self, transcript: list[dict], reason: str) -> tuple[dict, Usage]:
        return template_summary(transcript, reason), Usage("summary", "template")


class ModelEngine:
    """Claude. ``replay_only`` engines can never spend: a cassette miss raises ``ReplayMiss``."""

    def __init__(self, settings: Settings, cassette: Cassette, budget: Budget, replay_only: bool, client=None):
        self.s, self.cassette, self.budget, self.replay_only = settings, cassette, budget, replay_only
        self.name = "recorded" if replay_only else "live"
        self.gate: LiveGate | None = None  # set on a per-message copy
        self._client = client
        if client is None and not replay_only and settings.anthropic_key:
            self._client = self._make_client()  # at startup, not during the first customer's message

    def _make_client(self):
        import anthropic

        return anthropic.Anthropic(api_key=self.s.anthropic_key, timeout=30, max_retries=1)

    def _anthropic(self):
        if self.replay_only:
            raise ReplayMiss("no recorded response (recorded-answer mode never calls the API)")
        if self._client is None:
            if not self.s.anthropic_key:
                raise LLMError("ANTHROPIC_API_KEY is not set")
            self._client = self._make_client()
        if self.budget.exhausted():
            raise AllowanceExhausted("global")
        if self.gate:
            self.gate.check()
        return self._client

    def _lookup(self, key: str) -> dict | None:
        # live mode re-asks the API; replay, auto and record serve the cassette
        return self.cassette.entries.get(key) if self.s.llm_mode != "live" or self.replay_only else None

    def _finish(self, usage: Usage, key: str, text: str, record: bool = True) -> None:
        usage.cost_usd = cost_of(usage.model, usage.input_tokens, usage.output_tokens)
        self.budget.add(usage.cost_usd)
        if record and self.s.llm_mode == "record":
            self.cassette.add({"key": key, "role": usage.role, "model": usage.model, "text": text,
                               "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                               "latency_ms": round(usage.latency_ms), "first_token_ms": round(usage.first_token_ms)})

    def _json(self, role: str, model: str, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, Usage]:
        key = self.cassette.key(role, model, system, user)
        hit = self._lookup(key)
        if hit:
            return json.loads(hit["text"]), Usage(role, model, hit["input_tokens"], hit["output_tokens"], cached=True)
        client = self._anthropic()
        start = time.perf_counter()
        try:
            msg = client.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
            text = "".join(b.text for b in msg.content if b.type == "text")
            data = json.loads(text)
        except Exception as exc:  # any provider or parsing failure: the caller falls back to rules/templates
            raise LLMError(f"{role} call failed: {type(exc).__name__}") from exc
        usage = Usage(role, model, msg.usage.input_tokens, msg.usage.output_tokens, (time.perf_counter() - start) * 1000)
        self._finish(usage, key, text)
        return data, usage

    def triage(self, message: str) -> tuple[dict, Usage]:
        return self._json("triage", self.s.triage_model, prompts.TRIAGE_SYSTEM, f"Customer message:\n{message}",
                          prompts.TRIAGE_SCHEMA, 200)

    def stream_answer(self, question: str, hits: list[Hit], previous: str, usage: list[Usage]) -> Iterator[str]:
        model = self.s.answer_model
        user = prompts.answer_user(question, [(i, h.title, h.text) for i, h in enumerate(hits, 1)], previous)
        key = self.cassette.key("answer", model, prompts.ANSWER_SYSTEM, user)
        hit = self._lookup(key)
        if hit:
            usage.append(Usage("answer", model, hit["input_tokens"], hit["output_tokens"], cached=True))
            yield from re.findall(r"\S+\s*", hit["text"])
            return
        client = self._anthropic()
        start, first, parts = time.perf_counter(), 0.0, []
        try:
            with client.messages.stream(
                    model=model, max_tokens=500, system=prompts.ANSWER_SYSTEM, messages=[{"role": "user", "content": user}],
                    thinking={"type": "between_tools"}, output_config={"effort": "low"}) as stream:
                for delta in stream.text_stream:
                    first = first or (time.perf_counter() - start) * 1000
                    parts.append(delta)
                    yield delta
                final = stream.get_final_message()
        except Exception as exc:
            raise LLMError(f"answer call failed: {type(exc).__name__}") from exc
        record = Usage("answer", model, final.usage.input_tokens, final.usage.output_tokens,
                       (time.perf_counter() - start) * 1000, first)
        usage.append(record)
        self._finish(record, key, "".join(parts), record=final.stop_reason == "end_turn")
        if final.stop_reason == "refusal":
            raise LLMError("the model declined to answer")

    def summarize(self, transcript: list[dict], reason: str) -> tuple[dict, Usage]:
        return self._json("summary", self.s.summary_model, prompts.SUMMARY_SYSTEM,
                          prompts.summary_user(format_transcript(transcript), reason), prompts.SUMMARY_SCHEMA, 400)


def build_engines(settings: Settings) -> tuple[dict, Budget]:
    budget = Budget(settings.daily_spend_cap_usd)
    engines: dict = {"rules": RuleEngine()}
    names = settings.engines
    if "recorded" in names or "live" in names:  # in auto mode the "live" engine serves recordings first
        name = names[0]
        engines[name] = ModelEngine(settings, Cassette(settings.cassette), budget, replay_only=name == "recorded")
    return engines, budget

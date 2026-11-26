"""The model engine against a fake client: record, replay (never spends), spend cap and failure handling."""
import json
from types import SimpleNamespace as NS

import pytest

from app.config import Settings
from app.llm import AllowanceExhausted, Budget, Cassette, LiveGate, LLMError, ModelEngine, ReplayMiss
from app.retrieval import Hit
from app.sessions import MemorySessions

HITS = [Hit(1, "shipping-options", "Shipping options and costs", "Express shipping takes 2 business days and costs $14.95.", 0.8)]


class FakeStream:
    def __init__(self, chunks):
        self.text_stream = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return NS(usage=NS(input_tokens=1000, output_tokens=100), stop_reason="end_turn")


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps({"intent": "question", "sentiment": "neutral", "urgency": "low"})
        return NS(content=[NS(type="text", text=text)], usage=NS(input_tokens=400, output_tokens=30))

    def stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(["Express costs ", "$14.95 [1]."])


def engine(tmp_path, mode, replay_only=False, client=None, cap=1.0):
    s = Settings(llm_mode=mode, cassette=str(tmp_path / "c.jsonl"))
    return ModelEngine(s, Cassette(s.cassette), Budget(cap), replay_only, client)


def test_record_then_replay_without_any_call(tmp_path):
    client = FakeClient()
    rec = engine(tmp_path, "record", client=client)
    usage = []
    rec.triage("express price?")
    assert "".join(rec.stream_answer("express price?", HITS, "", usage)) == "Express costs $14.95 [1]."
    assert usage[0].cost_usd == pytest.approx((1000 * 2 + 100 * 10) / 1e6)
    assert client.calls[1]["thinking"] == {"type": "between_tools"} and "temperature" not in client.calls[1]

    replay = engine(tmp_path, "replay", replay_only=True)
    usage = []
    data, u = replay.triage("express price?")
    assert data["intent"] == "question" and u.cached and u.cost_usd == 0
    assert "".join(replay.stream_answer("express price?", HITS, "", usage)) == "Express costs $14.95 [1]."
    assert usage[0].cached and usage[0].cost_usd == 0


def test_replay_miss_never_reaches_the_network(tmp_path):
    replay = engine(tmp_path, "replay", replay_only=True, client=FakeClient())
    with pytest.raises(ReplayMiss):
        replay.triage("something new")
    with pytest.raises(ReplayMiss):
        list(replay.stream_answer("something new", HITS, "", []))
    assert replay._client.calls == []


def test_spend_cap_blocks_calls(tmp_path):
    client = FakeClient()
    live = engine(tmp_path, "live", client=client, cap=0.001)
    live.budget.add(0.002)
    with pytest.raises(LLMError, match="cap"):
        live.triage("x")
    assert client.calls == []


def test_provider_errors_become_llm_errors(tmp_path):
    class Broken:
        messages = property(lambda self: self)

        def create(self, **kw):
            raise TimeoutError()

    with pytest.raises(LLMError, match="TimeoutError"):
        engine(tmp_path, "live", client=Broken()).triage("x")


def test_auto_mode_serves_recordings_free_and_calls_only_on_a_miss(tmp_path):
    engine(tmp_path, "record", client=FakeClient()).triage("recorded one")
    client = FakeClient()
    auto = engine(tmp_path, "auto", client=client, cap=0)  # no allowance at all
    assert auto.triage("recorded one")[1].cached and client.calls == []
    with pytest.raises(AllowanceExhausted):
        auto.triage("something new")
    funded = engine(tmp_path, "auto", client=client)
    assert not funded.triage("something new")[1].cached and len(client.calls) == 1
    assert "something new" not in (tmp_path / "c.jsonl").read_text()  # auto never records


def test_visitor_limit_counts_messages_not_calls():
    sessions = MemorySessions()
    first = LiveGate(sessions, "203.0.113.9", 1)
    first.check()
    first.check()  # same message
    with pytest.raises(AllowanceExhausted) as caught:
        LiveGate(sessions, "203.0.113.9", 1).check()
    assert caught.value.scope == "visitor"
    LiveGate(sessions, "198.51.100.2", 1).check()  # someone else is unaffected
    assert not any("203.0.113.9" in key for key in sessions._counts)


def test_auto_engines_depend_on_the_key(tmp_path):
    (tmp_path / "c.jsonl").write_text("")
    path = str(tmp_path / "c.jsonl")
    assert Settings(llm_mode="auto", anthropic_key="k", cassette=path).engines == ["live", "rules"]
    assert Settings(llm_mode="auto", anthropic_key="", cassette=path).engines == ["recorded", "rules"]

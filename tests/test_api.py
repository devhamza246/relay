import json
from types import SimpleNamespace as NS

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.llm import Budget, Cassette, ModelEngine


@pytest.fixture
def client(make_machine):
    machine = make_machine(admin_token="t0ken", rate_limit_per_min=100, llm_mode="rules")
    return TestClient(create_app(machine.s, machine))


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_chat_streams_tokens_then_done(client):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    resp = client.post(f"/api/sessions/{sid}/messages", json={"text": "I was charged twice"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    got = events(resp)
    assert [e["type"] for e in got][-1] == "done" and any(e["type"] == "token" for e in got)
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["state"] == "escalated" and detail["handoffs"][0]["delivery"] == "dry_run"


def test_validation(client):
    assert client.post("/api/sessions", json={"customer": "x@example.com"}).status_code == 422
    assert client.post("/api/sessions", json={"engine": "live"}).status_code == 422
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.post(f"/api/sessions/{sid}/messages", json={"text": "x" * 1001}).status_code == 422
    assert client.post("/api/sessions/nope/messages", json={"text": "hi"}).status_code == 404
    assert client.get("/api/sessions/00000000-0000-0000-0000-000000000000").status_code == 404


def test_status_health_and_articles(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    status = client.get("/api/status").json()
    assert status["engines"] == ["rules"] and status["slack"] == "dry_run" and status["articles"] >= 50
    assert "30 days" in client.get("/api/articles/return-window").json()["body"]
    assert client.get("/api/articles/nope").status_code == 404


def test_admin_export_needs_the_token(client):
    assert client.get("/api/admin/export").status_code == 401
    assert client.get("/api/admin/export", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/admin/export?n=2", headers={"Authorization": "Bearer t0ken"}).status_code == 200


def test_rate_limit(make_machine):
    machine = make_machine(rate_limit_per_min=2)
    client = TestClient(create_app(machine.s, machine))
    codes = [client.post("/api/sessions", json={}).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


class Fake:
    """Stands in for the Anthropic client so the whole API path can be driven without a network."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        keys = kw["output_config"]["format"]["schema"]["required"]
        data = {"intent": "question", "sentiment": "neutral", "urgency": "low"} if "intent" in keys else dict.fromkeys(keys, "x")
        return NS(content=[NS(type="text", text=json.dumps(data))], usage=NS(input_tokens=800, output_tokens=30))

    def stream(self, **kw):
        final = NS(usage=NS(input_tokens=900, output_tokens=40), stop_reason="end_turn")

        class Stream:
            text_stream = iter(["Express shipping costs $14.95 [1]."])
            __enter__ = lambda self: self
            __exit__ = lambda self, *a: False
            get_final_message = lambda self: final

        return Stream()


@pytest.mark.parametrize("cap,per_day,scope", [(0.0, 8, "global"), (1.0, 1, "visitor")])
def test_used_up_allowance_is_friendly_and_not_a_failed_answer(make_machine, tmp_path, cap, per_day, scope):
    machine = make_machine(llm_mode="auto", anthropic_key="k", cassette=str(tmp_path / "c.jsonl"),
                           daily_spend_cap_usd=cap, visitor_live_per_day=per_day)
    machine.engines["live"] = ModelEngine(machine.s, Cassette(machine.s.cassette), Budget(cap), False, Fake())
    client = TestClient(create_app(machine.s, machine))
    sid = client.post("/api/sessions", json={}).json()["session_id"]

    def ask():
        return events(client.post(f"/api/sessions/{sid}/messages", json={"text": "what does express delivery cost"}))[-1]

    if scope == "visitor":
        assert ask()["outcome"] == "answered"
    done = [ask(), ask(), ask()][-1]
    assert done["outcome"] == "allowance_used" and done["allowance"] == scope
    assert done["failed_answers"] == 0 and "used" in done["text"] and "Error" not in done["text"]
    left = client.get("/api/status").json()["live_allowance"]
    assert (left["remaining_usd"] == 0) if scope == "global" else (left["visitor_remaining"] == 0)


KEY = "sk-ant-test-" + "Zq7" * 12


def keyed_app(make_machine, tmp_path, monkeypatch, client_factory):
    """Server with no allowance at all (cap 0) in record mode, the mode that writes cassettes."""
    machine = make_machine(llm_mode="record", anthropic_key="server-key", cassette=str(tmp_path / "c.jsonl"),
                           daily_spend_cap_usd=0.0, visitor_live_per_day=1, rate_limit_per_min=100)
    used = []

    def make_client(self, api_key=""):
        used.append(api_key or "server")
        return client_factory()

    monkeypatch.setattr(ModelEngine, "_make_client", make_client)
    machine.engines["live"] = ModelEngine(machine.s, Cassette(machine.s.cassette), Budget(0.0), False)
    return machine, TestClient(create_app(machine.s, machine)), used


def send(client, sid, text, key=None):
    return client.post(f"/api/sessions/{sid}/messages", json={"text": text}, headers={"X-Anthropic-Key": key} if key else {})


def test_own_key_bypasses_the_cap_and_is_stored_nowhere(make_machine, tmp_path, monkeypatch, caplog, database_url):
    caplog.set_level("DEBUG")
    machine, client, used = keyed_app(make_machine, tmp_path, monkeypatch, Fake)
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert events(send(client, sid, "what does express delivery cost"))[-1]["outcome"] == "allowance_used"

    answers = [send(client, sid, "what does express delivery cost", KEY) for _ in range(2)]  # beyond the visitor limit too
    handoff = send(client, sid, f"I was charged twice, and here is my key {KEY}", KEY)
    assert [events(r)[-1]["outcome"] for r in answers] == ["answered", "answered"] and KEY in used
    assert events(handoff)[-1]["outcome"] == "escalated"
    assert machine.engines["live"].budget.spent == 0 and machine.engines["live"].own_key == ""
    assert not (tmp_path / "c.jsonl").exists()  # nothing recorded, even in record mode

    stored = [r.text for r in (*answers, handoff, client.get(f"/api/sessions/{sid}"))]
    stored += [str(machine.sessions._data), str(machine.sessions._counts), caplog.text]
    with psycopg.connect(database_url) as conn:
        for table in ("conversations", "messages", "tickets", "handoffs"):
            stored.append(str(conn.execute(f"SELECT t::text FROM {table} t").fetchall()))
    assert not any(KEY in blob for blob in stored)


def test_a_rejected_own_key_is_friendly_and_not_a_failed_answer(make_machine, tmp_path, monkeypatch):
    class Rejected(Fake):
        def create(self, **kw):
            raise PermissionError(f"bad key {KEY}")

        def stream(self, **kw):
            raise PermissionError(f"bad key {KEY}")

    _, client, _ = keyed_app(make_machine, tmp_path, monkeypatch, Rejected)
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    for _ in range(3):
        resp = send(client, sid, "what does express delivery cost", KEY)
        done = events(resp)[-1]
        assert done["outcome"] == "key_error" and done["failed_answers"] == 0 and KEY not in resp.text


@pytest.mark.parametrize("bad", ["hello", "sk-ant-short", "sk-ant-" + "a" * 300, "sk-ant-has space in it 0000000000"])
def test_malformed_own_key_is_rejected_without_echo(client, bad):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    resp = send(client, sid, "hi", bad)
    assert resp.status_code == 400 and bad not in resp.text

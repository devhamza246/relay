import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app


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

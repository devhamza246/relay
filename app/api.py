"""HTTP API. Chat replies stream as Server-Sent Events over POST."""
from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .embed import Embedder
from .llm import build_engines, utc_day
from .machine import Machine
from .retrieval import Retriever
from .sessions import Busy, RedisSessions
from .slack import SlackClient
from .store import Store, make_pool

log = logging.getLogger("relay")

SUGGESTIONS = [
    {"label": "Returns", "turns": ["How many days do I have to send something back?"]},
    {"label": "Shipping cost", "turns": ["what does express delivery cost"]},
    {"label": "Order status", "turns": ["Where is my order FW-10482?"]},
    {"label": "Desk error", "turns": ["My standing desk shows E01 and won't move"]},
    {"label": "Billing dispute", "turns": ["I was charged twice for order FW-10482. I want the extra charge reversed."]},
    {"label": "Open a ticket", "turns": ["Please open a ticket: the desktop on my new desk has a scratch along one edge."]},
    {"label": "Unknown", "turns": ["Can I rent a standing desk for a month?"]},
]


class NewSession(BaseModel):
    customer: str = Field(default="", max_length=200)
    engine: str = Field(default="", max_length=20)


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


def create_app(settings: Settings | None = None, machine: Machine | None = None) -> FastAPI:
    s = settings or Settings()
    if machine is None:
        pool = make_pool(s.database_url)
        embedder = Embedder(s.embed_model)
        embedder.query("warm up")  # load the model before the first customer
        engines, _ = build_engines(s)
        machine = Machine(s, Store(pool), RedisSessions(s.redis_url), Retriever(pool, embedder), engines,
                          SlackClient(s.slack_webhook_url, s.slack_max_per_hour))
    app = FastAPI(title="Relay", version=__version__)
    app.state.machine = machine
    store, sessions = machine.store, machine.sessions

    def limit(request: Request, kind: str, per_minute: int) -> None:
        if per_minute > 0 and sessions.hit(f"{kind}:{client_ip(request)}") > per_minute:
            raise HTTPException(429, "Too many requests. Please wait a minute.")

    def session(sid: str) -> dict:
        try:
            uuid.UUID(sid)
        except ValueError:
            raise HTTPException(404, "unknown conversation") from None
        state = sessions.load(sid)
        if state is None:
            raise HTTPException(404, "unknown or expired conversation")
        return state

    def stream_turn(sid: str, text: str, visitor: str):
        """The turn runs in its own thread, so a handoff still completes if the browser disconnects mid-reply."""
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                for event in machine.handle(sid, text, visitor):
                    events.put(event)
            except Busy:
                events.put({"type": "error", "text": "Still answering your previous message. Please wait a moment."})
            except Exception:
                log.exception("turn failed")
                events.put({"type": "error", "text": "Something went wrong on our side. Please try again."})
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        while (event := events.get()) is not None:
            yield sse(event)

    @app.get("/healthz")
    def healthz() -> dict:
        try:
            store.ping()
            sessions.ping()
        except Exception as exc:
            raise HTTPException(503, "a dependency is unavailable") from exc
        return {"status": "ok"}

    def allowance(request: Request) -> dict | None:
        """What is left of today's live budget, for the status line. None when no live calls are possible."""
        budget = getattr(machine.engines.get("live"), "budget", None)
        if budget is None:
            return None
        used = sessions.day_count(client_ip(request), utc_day().isoformat())
        return {"daily_usd": budget.cap, "remaining_usd": round(max(0.0, budget.cap - budget.spent), 4),
                "visitor_per_day": s.visitor_live_per_day,
                "visitor_remaining": max(0, s.visitor_live_per_day - used) if s.visitor_live_per_day > 0 else None,
                "resets": "00:00 UTC"}

    @app.get("/api/status")
    def status(request: Request) -> dict:
        return {"mode": s.llm_mode, "live_allowance": allowance(request), "version": __version__, "engines": s.engines, "slack": machine.slack.mode, "articles": store.article_count(),
                "customers": store.customers(), "suggestions": SUGGESTIONS, "retention_hours": s.retention_hours,
                "models": {"triage": s.triage_model, "answer": s.answer_model, "summary": s.summary_model}}

    @app.post("/api/sessions", status_code=201)
    def new_session(body: NewSession, request: Request) -> dict:
        limit(request, "session", s.rate_limit_per_min)
        customers = {c["email"] for c in store.customers()}
        customer = body.customer or "maya.chen@example.com"
        if customer not in customers:
            raise HTTPException(422, "unknown demo customer")
        engine = body.engine or s.engines[0]
        if engine not in s.engines:
            raise HTTPException(422, f"engine must be one of {s.engines}")
        return {"session_id": machine.start(customer, engine), "engine": engine, "customer": customer}

    @app.post("/api/sessions/{sid}/messages")
    def message(sid: str, body: NewMessage, request: Request) -> StreamingResponse:
        limit(request, "message", s.rate_limit_per_min)
        state = session(sid)
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "empty message")
        if len(text) > s.max_message_chars:
            raise HTTPException(422, f"message is longer than {s.max_message_chars} characters")
        if state["turns"] >= s.max_messages_per_conversation:
            raise HTTPException(429, "This demo conversation is full. Please start a new one.")

        return StreamingResponse(stream_turn(sid, text, client_ip(request)), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/api/sessions/{sid}")
    def transcript(sid: str) -> dict:
        """The conversation ID is a random secret: only its holder sees the transcript, tickets and Slack payload."""
        state = session(sid)
        return {"state": state["state"], "failed_answers": state["failed"], "messages": store.messages(sid),
                "tickets": store.tickets(sid), "handoffs": store.handoffs(sid)}

    @app.get("/api/articles/{slug}")
    def article(slug: str) -> dict:
        found = store.article(slug)
        if not found:
            raise HTTPException(404, "no such article")
        return found

    def admin(authorization: str = Header(default="")) -> None:
        if not s.admin_token:
            raise HTTPException(404, "not found")
        if not secrets.compare_digest(authorization.encode(), f"Bearer {s.admin_token}".encode()):
            raise HTTPException(401, "bad token")

    @app.get("/api/admin/export", dependencies=[Depends(admin)])
    def export(n: int = 200) -> list[dict]:
        """Logged conversations for building evaluations."""
        return store.export(min(max(n, 1), 1000))

    return app


def _retention_loop(store: Store, hours: int) -> None:
    while True:
        try:
            removed = store.purge(hours)
            if removed:
                log.info("retention: removed %s conversations", removed)
        except Exception:
            log.exception("retention failed")
        time.sleep(3600)


def get_app() -> FastAPI:
    app = create_app()
    s = Settings()
    if s.retention_hours > 0:
        threading.Thread(target=_retention_loop, args=(app.state.machine.store, s.retention_hours), daemon=True).start()
    return app

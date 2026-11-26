"""The support conversation as an explicit state machine (plain code, no graph framework).

    message -> redact -> [already escalated?] -> triage -> ESCALATION CHECK -> route
    route: negative feedback | order tool | ticket tool | small talk | retrieve -> gated cited answer -> low confidence

Conversation states: open, awaiting_order_id, offered_human, escalated (terminal for the bot).
``handle`` is a generator of events: {"type": "status"|"token"|"done", ...}.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Iterator

from . import prompts, rules
from .citations import SentenceSplitter, check_sentence
from .config import Settings
from .llm import LLMError, ReplayMiss, RuleEngine, Usage, format_transcript, template_summary
from .redact import redact
from .retrieval import Retriever
from .sessions import new_state
from .slack import SlackClient, build_payload
from .store import Store, ticket_code

OFFER = "Would you like me to pass this to a human support agent?"
UNKNOWN = "I don't know the answer to that from our help center, so I won't guess. " + OFFER
FEEDBACK = "Sorry that didn't help. You can rephrase the question, or I can pass this to a human support agent. Which would you like?"
NOT_RECORDED = ("This public demo replays recorded model answers and never calls the paid API, and that question was not recorded. "
                "Try one of the suggested questions, or switch the engine to \"Rule-based\" to type anything.")
SMALLTALK_REPLY = "Hi, I'm Relay, the Fernwick Goods support assistant. Ask me about orders, returns, billing, your account or our products."
THANKS_REPLY = "You're welcome. Is there anything else I can help with?"
ASK_ORDER = "I can check that. What is the order number? It looks like FW-12345."
DECLINED = "No problem. Ask me anything else whenever you're ready."
HANDOFF_LEAD = {
    "billing_dispute": "A billing problem needs a person, so I'm handing this over.",
    "legal": "This needs a person on our team, so I'm handing it over.",
    "angry": "I'm sorry about this experience. I'm handing you to a person.",
    "human_request": "Sure, I'm handing you to a person.",
    "repeat_failure": "I haven't managed to answer this, so I'm handing you to a person.",
    "security": "Account security needs a person, so I'm handing this over.",
    "safety": "Your safety comes first: if anyone is in danger, contact your local emergency services. I'm handing this to a person.",
}


class Machine:
    def __init__(self, settings: Settings, store: Store, sessions, retriever: Retriever, engines: dict, slack: SlackClient):
        self.s, self.store, self.sessions, self.retriever = settings, store, sessions, retriever
        self.engines, self.slack = engines, slack
        self.rules_engine = engines.get("rules") or RuleEngine()

    def start(self, customer: str, engine: str) -> str:
        sid = str(uuid.uuid4())
        self.store.create_conversation(sid, customer, engine)
        self.sessions.save(sid, new_state(customer, engine))
        return sid

    def handle(self, sid: str, text: str) -> Iterator[dict]:
        with self.sessions.lock(sid):
            state = self.sessions.load(sid)
            if state is None:
                raise KeyError(sid)
            turn = _Turn(self, sid, state, redact(text.strip()))
            yield from turn.run()

    def run(self, sid: str, text: str) -> dict:
        """Non-streaming convenience for tests and evals: the final event, plus timings."""
        start, first, done = time.perf_counter(), None, {}
        for event in self.handle(sid, text):
            if event["type"] == "token" and first is None:
                first = (time.perf_counter() - start) * 1000
            if event["type"] == "done":
                done = event
        done["first_token_ms"] = round(first or 0.0, 1)
        done["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return done


class _Turn:
    def __init__(self, machine: Machine, sid: str, state: dict, message: str):
        self.m, self.sid, self.state, self.message = machine, sid, state, message
        self.engine = machine.engines.get(state["engine"]) or machine.rules_engine
        self.usage: list[Usage] = []
        self.notes: list[str] = []
        self.reply = ""
        self.meta: dict = {}
        self.started = time.perf_counter()

    # -- plumbing -------------------------------------------------------------------------------------------------
    def say(self, text: str) -> Iterator[dict]:
        piece = (" " if self.reply else "") + text
        self.reply += piece
        yield {"type": "token", "text": piece}

    def finish(self, outcome: str, **extra) -> Iterator[dict]:
        self.state["turns"] += 1
        data = {"outcome": outcome, "state": self.state["state"], "failed_answers": self.state["failed"],
                "engine": self.engine.name, **self.meta, **extra}
        self.m.sessions.save(self.sid, self.state)
        self.m.store.set_state(self.sid, self.state["state"])
        self.m.store.add_message(self.sid, "assistant", self.reply, {
            **data, "usage": [u.to_dict() for u in self.usage], "notes": self.notes,
            "latency_ms": round((time.perf_counter() - self.started) * 1000, 1)})
        yield {"type": "done", "text": self.reply, "notes": self.notes, "usage": [u.to_dict() for u in self.usage], **data}

    def run(self) -> Iterator[dict]:
        self.m.store.add_message(self.sid, "customer", self.message)
        if self.state["state"] == "escalated":
            self.m.store.append_to_ticket(self.state["ticket_id"], self.message)
            yield from self.say(f"A human agent has this conversation under ticket {ticket_code(self.state['ticket_id'])}. "
                                "I've added your message to it.")
            yield from self.finish("with_human", ticket=ticket_code(self.state["ticket_id"]))
            return

        yield {"type": "status", "text": "Reading your message"}
        triage = self.triage()
        self.meta["triage"] = triage

        # 1. Escalation rules come first. Lexical signals always count; the model's triage can add to them.
        reasons = rules.escalation_signals(self.message)
        if triage["intent"] in rules.ESCALATION_REASONS:
            reasons.append(triage["intent"])
        if triage["sentiment"] == "angry":
            reasons.append("angry")
        offered = self.state["state"] == "offered_human"
        if offered and rules.AFFIRM.match(self.message):
            reasons.append("human_request")
        if reasons:
            reason = min(reasons, key=rules.ESCALATION_REASONS.index)
            self.meta["signals"] = sorted(set(reasons))
            yield from self.escalate(reason)
            return
        if offered and rules.DECLINE.match(self.message):
            self.state["state"] = "open"
            yield from self.say(DECLINED)
            yield from self.finish("smalltalk")
            return

        # 2. "That didn't help" counts as a failed answer.
        if triage["intent"] == "negative_feedback" or rules.NEGATIVE_FEEDBACK.search(self.message):
            yield from self.failed(FEEDBACK, "feedback")
            return

        # 3. Tools.
        order = rules.order_id(self.message)
        if triage["intent"] == "create_ticket" or rules.CREATE_TICKET.search(self.message):
            yield from self.open_ticket(triage)
            return
        awaiting = self.state["state"] == "awaiting_order_id"
        if order and (awaiting or triage["intent"] == "order_status"):
            yield from self.order_status(order)
            return
        if triage["intent"] == "order_status":
            self.state["state"] = "awaiting_order_id"
            yield from self.say(ASK_ORDER)
            yield from self.finish("need_order_id")
            return

        if rules.SMALLTALK.match(self.message) or triage["intent"] == "smalltalk":
            self.state["state"] = "open"
            thanks = self.message.lower().startswith(("thank", "cheers", "great", "perfect", "awesome"))
            yield from self.say(THANKS_REPLY if thanks else SMALLTALK_REPLY)
            yield from self.finish("smalltalk")
            return

        # 4. Answer from the help center.
        yield from self.answer()

    # -- steps ----------------------------------------------------------------------------------------------------
    def triage(self) -> dict:
        try:
            data, usage = self.engine.triage(self.message)
            self.usage.append(usage)
            return {"intent": data["intent"], "sentiment": data["sentiment"], "urgency": data["urgency"], "source": usage.model}
        except (LLMError, KeyError, TypeError) as exc:
            if not isinstance(exc, ReplayMiss):
                self.notes.append("Model triage was unavailable; rule-based triage was used.")
            return {**rules.classify(self.message), "source": "rule-based"}

    def answer(self) -> Iterator[dict]:
        yield {"type": "status", "text": "Searching the help center"}
        # A very short follow-up ("what about Canada?") is searched together with the previous question.
        follow_up = len(self.message.split()) <= 5 and self.message.lower().startswith(("and ", "what about", "how about", "what if"))
        previous = self.state["previous_question"] if follow_up else ""
        found = self.m.retriever.search(f"{previous} {self.message}".strip(), k=self.m.s.retrieval_k)
        self.meta["confidence"] = found.confidence
        self.meta["retrieved"] = [{"slug": h.slug, "similarity": h.similarity} for h in found.hits]
        threshold = self.m.s.confidence_threshold if self.engine.name == "rules" else self.m.s.model_confidence_floor
        if found.confidence < threshold:
            yield from self.failed(UNKNOWN, "low_confidence")
            return

        sources = {i: f"{h.title}. {h.text}" for i, h in enumerate(found.hits, 1)}
        splitter, cited, dropped, text = SentenceSplitter(), [], [], []
        first_model_text = None

        def release(sentences: list[str]) -> Iterator[dict]:
            for sentence in sentences:
                if sentence.strip().startswith(prompts.NO_ANSWER):
                    continue
                verdict = check_sentence(sentence, sources, self.message)
                if verdict.ok:
                    cited.extend(n for n in verdict.cited if n not in cited)
                    yield from self.say(sentence)
                else:
                    dropped.append({"sentence": sentence, "reason": verdict.reason})

        try:
            for delta in self.engine.stream_answer(self.message, found.hits, previous, self.usage):
                first_model_text = first_model_text or round((time.perf_counter() - self.started) * 1000, 1)
                text.append(delta)
                yield from release(splitter.feed(delta))
            yield from release(splitter.flush())
        except ReplayMiss:
            yield from self.say(NOT_RECORDED)
            yield from self.finish("not_recorded")
            return
        except LLMError as exc:
            self.notes.append(f"The answer model failed ({exc}).")
            if not self.reply:
                yield from self.failed("I couldn't produce an answer just now. " + OFFER, "model_error")
                return

        self.meta["model_first_token_ms"] = first_model_text
        self.meta["dropped_sentences"] = dropped
        if not self.reply:
            yield from self.failed(UNKNOWN, "no_answer" if "".join(text).strip().startswith(prompts.NO_ANSWER) else "uncited")
            return
        self.state["state"] = "open"
        self.state["previous_question"] = self.message
        citations = [{"n": n, "kind": "article", "slug": found.hits[n - 1].slug, "title": found.hits[n - 1].title} for n in sorted(cited)]
        yield from self.finish("answered", citations=citations)

    def failed(self, reply: str, why: str) -> Iterator[dict]:
        self.state["failed"] += 1
        self.meta["failure"] = why
        if self.state["failed"] >= self.m.s.max_failed_answers:
            yield from self.escalate("repeat_failure")
            return
        self.state["state"] = "offered_human"
        yield from self.say(reply)
        yield from self.finish("unknown")

    def order_status(self, order: str) -> Iterator[dict]:
        yield {"type": "status", "text": f"Looking up order {order}"}
        record = self.m.store.order(order, self.state["customer"])
        self.meta["tool"] = {"name": "order_status", "order": order, "found": bool(record)}
        if record is None:
            self.meta["failure"] = "order_not_found"
            self.state["state"] = "awaiting_order_id"
            self.state["failed"] += 1
            if self.state["failed"] >= self.m.s.max_failed_answers:
                yield from self.escalate("repeat_failure")
                return
            yield from self.say(f"I can't find order {order} on your account. Please check the number, or ask me for a human agent.")
            yield from self.finish("unknown")
            return
        self.state["state"] = "open"
        items = ", ".join(record["items"])
        yield from self.say(f"Order {record['id']} ({items}, ${record['total']}) is {record['status']} [1].")
        for sentence in re.split(r"(?<=[.!?])\s+", record["detail"].strip()):
            yield from self.say(f"{sentence.rstrip('.')} [1].")
        yield from self.finish("order_status", citations=[
            {"n": 1, "kind": "order", "slug": record["id"], "title": f"Order system (mock): {record['id']}", "record": record}])

    def open_ticket(self, triage: dict) -> Iterator[dict]:
        ticket_id = self.m.store.create_ticket(self.sid, "request", triage["urgency"], self.message,
                                               format_transcript(self.m.store.messages(self.sid)))
        code = ticket_code(ticket_id)
        self.state["state"] = "open"
        self.meta["tool"] = {"name": "create_ticket", "ticket": code}
        yield from self.say(f"I opened ticket {code} with your message [1].")
        yield from self.finish("ticket_created", ticket=code, citations=[
            {"n": 1, "kind": "ticket", "slug": code, "title": f"Ticket {code}"}])

    def escalate(self, reason: str) -> Iterator[dict]:
        yield {"type": "status", "text": "Preparing the handoff"}
        yield from self.say(HANDOFF_LEAD[reason])
        transcript = [{"role": m["role"], "text": m["text"]} for m in self.m.store.messages(self.sid)]
        try:
            summary, usage = self.engine.summarize(transcript, reason)
            self.usage.append(usage)
            summary = {k: str(summary[k]) for k in ("summary", "suspected_intent", "next_step")}
        except (LLMError, KeyError, TypeError):
            summary = template_summary(transcript, reason)
            self.notes.append("Template summary used (no model summary available).")
        triage = self.meta.get("triage", {})
        urgency = "high" if reason not in ("human_request", "repeat_failure") else triage.get("urgency", "normal")
        sentiment = "angry" if "angry" in self.meta.get("signals", []) else triage.get("sentiment", "neutral")
        ticket_id = self.m.store.create_ticket(self.sid, "handoff", urgency, f"{reason}: {self.message}", format_transcript(transcript))
        code = ticket_code(ticket_id)
        payload = build_payload(ticket=code, reason=reason, urgency=urgency, sentiment=sentiment,
                                customer=self.state["customer"], summary=summary, transcript=transcript)
        handoff_id = self.m.store.create_handoff(self.sid, ticket_id, reason, summary, payload)
        delivery, detail = self.m.slack.deliver(payload)  # after the ticket is saved, so a Slack failure loses nothing
        self.m.store.set_delivery(handoff_id, delivery, detail)
        self.state.update(state="escalated", ticket_id=ticket_id)
        yield from self.say(f"Your ticket is {code}. The agent gets this conversation and a summary, so you won't need to repeat yourself.")
        yield from self.finish("escalated", ticket=code, reason=reason, handoff={"delivery": delivery, "detail": detail})

"""The state machine end to end against the real database, plus least-privilege checks."""
import psycopg
import pytest

from app.llm import LLMError, ReplayMiss, RuleEngine, Usage
from app.sessions import Busy

from .conftest import MAYA

LEO = "leo.ortiz@example.com"


def talk(machine, *turns, customer=MAYA, engine="rules"):
    sid = machine.start(customer, engine)
    return sid, [machine.run(sid, t) for t in turns]


def test_answers_with_citations(machine):
    _, [r] = talk(machine, "How much does express delivery cost?")
    assert r["outcome"] == "answered" and "$14.95" in r["text"]
    assert r["citations"][0]["slug"] == "shipping-options" and "[1]" in r["text"]


def test_order_tool_is_scoped_to_the_signed_in_customer(machine):
    _, [mine] = talk(machine, "Where is my order FW-10482?")
    assert mine["outcome"] == "order_status" and "shipped" in mine["text"]
    _, [theirs] = talk(machine, "Where is my order FW-10482?", customer=LEO)
    assert theirs["outcome"] == "unknown" and "ParcelOne" not in theirs["text"]


def test_asks_for_the_order_number_then_uses_it(machine):
    _, [ask, got] = talk(machine, "Can you check on my order?", "It's FW-10591")
    assert ask["outcome"] == "need_order_id" and got["outcome"] == "order_status" and "cancelled" in got["text"]


def test_billing_dispute_opens_a_ticket_and_records_the_slack_payload(machine):
    sid, [r] = talk(machine, "I was charged twice for order FW-10482, card 4242 4242 4242 4242")
    assert r["outcome"] == "escalated" and r["reason"] == "billing_dispute" and r["handoff"]["delivery"] == "dry_run"
    [ticket] = machine.store.tickets(sid)
    [handoff] = machine.store.handoffs(sid)
    assert ticket["code"] == r["ticket"] and ticket["priority"] == "high"
    assert ticket["code"] in handoff["payload"]["text"] and "charged twice" in str(handoff["payload"])
    assert "4242 4242" not in str(machine.store.messages(sid)) + str(handoff["payload"])


def test_two_failed_answers_escalate(machine):
    _, [first, second] = talk(machine, "Do you sell treadmill desks?", "What colour is your delivery van?")
    assert first["outcome"] == "unknown" and first["state"] == "offered_human"
    assert second["outcome"] == "escalated" and second["reason"] == "repeat_failure"


def test_accepting_the_offer_escalates_and_the_bot_then_stays_quiet(machine):
    sid, [_, yes, later] = talk(machine, "What is the capital of France?", "yes please", "How much is express shipping?")
    assert yes["reason"] == "human_request" and later["outcome"] == "with_human" and "$" not in later["text"]
    assert "express shipping" in machine.store.tickets(sid)[0]["body"]


class Scripted(RuleEngine):
    name = "scripted"

    def __init__(self, answer="", triage_error=None, answer_error=None, intent="question", sentiment="neutral"):
        self.answer, self.triage_error, self.answer_error = answer, triage_error, answer_error
        self.intent, self.sentiment = intent, sentiment

    def triage(self, message):
        if self.triage_error:
            raise self.triage_error
        return {"intent": self.intent, "sentiment": self.sentiment, "urgency": "low"}, Usage("triage", "scripted")

    def stream_answer(self, question, hits, previous, usage):
        if self.answer_error:
            raise self.answer_error
        yield from self.answer

    def summarize(self, transcript, reason):
        raise LLMError("down")


QUESTION = "what does express delivery cost"


def test_uncited_model_sentences_never_reach_the_customer(make_machine):
    answer = "Great question! Express shipping costs $14.95 [1]. We also ship by drone. It costs $99 [1]."
    machine = make_machine({"x": Scripted(answer)})
    _, [r] = talk(machine, QUESTION, engine="x")
    assert r["text"] == "Express shipping costs $14.95 [1]." and len(r["dropped_sentences"]) == 3


@pytest.mark.parametrize("answer", ["NO_ANSWER", "We love shipping things."])
def test_nothing_citable_counts_as_a_failed_answer(make_machine, answer):
    _, [r] = talk(make_machine({"x": Scripted(answer)}), QUESTION, engine="x")
    assert r["outcome"] == "unknown" and r["failed_answers"] == 1 and "don't know" in r["text"]


def test_rules_escalate_even_if_the_model_misses_it_or_is_down(make_machine):
    for stand_in in (Scripted(), Scripted(triage_error=ReplayMiss("x")), Scripted(triage_error=LLMError("x"))):
        _, [r] = talk(make_machine({"x": stand_in}), "My lawyer will be in touch", engine="x")
        assert r["outcome"] == "escalated" and r["reason"] == "legal"
        assert "Template summary" in " ".join(r["notes"])


def test_model_triage_can_add_an_escalation(make_machine):
    machine = make_machine({"x": Scripted(intent="question", sentiment="angry")})
    _, [r] = talk(machine, "well that is just lovely, thanks a bunch", engine="x")
    assert r["reason"] == "angry"


def test_replay_miss_is_friendly_and_not_a_failure(make_machine):
    machine = make_machine({"x": Scripted(answer_error=ReplayMiss("x"))})
    _, [r] = talk(machine, QUESTION, engine="x")
    assert r["outcome"] == "not_recorded" and r["failed_answers"] == 0 and "not recorded" in r["text"]


def test_one_turn_at_a_time(machine):
    sid = machine.start(MAYA, "rules")
    with machine.sessions.lock(sid):
        with pytest.raises(Busy):
            machine.run(sid, "hello")


@pytest.mark.parametrize("statement", [
    "DELETE FROM kb_chunks", "UPDATE orders SET status = 'delivered'", "INSERT INTO customers VALUES ('x@example.com', 'x')",
    "CREATE TABLE t (id int)", "DROP TABLE conversations",
])
def test_api_role_is_least_privilege(database_url, statement):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)

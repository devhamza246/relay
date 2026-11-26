"""Pure logic: help center, rules, citation gate, redaction, Slack."""
import httpx
import pytest

from app import rules
from app.citations import SentenceSplitter, check_sentence
from app.kb import load_articles
from app.redact import redact
from app.slack import SlackClient, build_payload
from eval.scoring import uncited_claims

SOURCES = {1: "Shipping options. Standard shipping takes 3 to 5 business days and costs $5.95."}


def test_help_center_size_and_chunks():
    articles = load_articles()
    assert 50 <= len(articles) <= 100
    assert all(a.chunks() for a in articles)


@pytest.mark.parametrize("text,reason", [
    ("I have been charged twice for one chair", "billing_dispute"),
    ("There is a payment I never authorised", "billing_dispute"),
    ("Expect to hear from my solicitor", "legal"),
    ("this is unacceptable", "angry"),
    ("WHY IS NOBODY ANSWERING MY EMAILS", "angry"),
    ("please connect me to a real person", "human_request"),
    ("I think my account was hacked", "security"),
    ("the charger is sparking", "safety"),
])
def test_escalation_signals(text, reason):
    assert reason in rules.escalation_signals(text)


@pytest.mark.parametrize("text", [
    "How do refunds work?", "When do you take payment?", "Could I be charged twice for a split order?",
    "How do I contact support about a warranty?", "Is there a fee if I cancel?",
])
def test_ordinary_questions_do_not_escalate(text):
    assert rules.escalation_signals(text) == []


def test_order_id_and_intent():
    assert rules.order_id("what about fw 10482?") == "FW-10482"
    assert rules.classify("where is my parcel")["intent"] == "order_status"
    assert rules.classify("Raise a ticket for me please")["intent"] == "create_ticket"


def test_splitter_keeps_prices_and_trailing_citations_across_deltas():
    splitter, out = SentenceSplitter(), []
    for delta in ["It costs $5", ".95 [1]. Delivery takes 3 days. ", "[1]", " Done [1]."]:
        out += splitter.feed(delta)
    out += splitter.flush()
    assert out == ["It costs $5.95 [1].", "Delivery takes 3 days. [1]", "Done [1]."]


@pytest.mark.parametrize("sentence,ok", [
    ("Standard shipping costs $5.95 [1].", True),
    ("Standard shipping costs $5.95.", False),            # no citation
    ("Standard shipping costs $7.50 [1].", False),        # invented figure
    ("Standard shipping costs $5.95 [3].", False),        # source not retrieved
    ("Our founder enjoys sailing yachts [1].", False),    # nothing in common with the source
])
def test_citation_gate(sentence, ok):
    assert check_sentence(sentence, SOURCES).ok is ok


def test_independent_scorer_flags_uncited_and_unknown_templates():
    cited = [{"n": 1, "kind": "article", "slug": "shipping-options"}]
    assert uncited_claims({"outcome": "answered", "text": "Express costs $14.95 [1].", "citations": cited}, "") == []
    assert uncited_claims({"outcome": "answered", "text": "Express costs $14.95. It is fast [1].", "citations": cited}, "")
    assert uncited_claims({"outcome": "answered", "text": "Express costs $99 [1].", "citations": cited}, "")
    assert uncited_claims({"outcome": "unknown", "text": "Sure, we ship to Mars."}, "")


def test_redaction():
    assert "4242 4242" not in redact("my card is 4242 4242 4242 4242 ok")
    assert redact("password: hunter2") == "password: [removed]"
    assert redact("order 1234567890123") == "order 1234567890123"  # fails the Luhn check: not a card


def payload():
    return build_payload(ticket="FWT-1001", reason="legal", urgency="high", sentiment="angry", customer="a@example.com",
                         summary={"summary": "s", "suspected_intent": "i", "next_step": "n"},
                         transcript=[{"role": "customer", "text": "<!channel> <http://evil|click>"}])


def test_slack_payload_escapes_customer_text():
    assert "<!channel>" not in str(payload()) and "&lt;!channel&gt;" in str(payload())


def test_slack_dry_run_sends_nothing():
    status, detail = SlackClient("").deliver(payload())
    assert status == "dry_run" and "nothing was sent" in detail


@pytest.mark.parametrize("code,expected", [(200, "sent"), (404, "failed"), (500, "failed")])
def test_slack_webhook(code, expected):
    seen = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(code))[1]))
    status, detail = SlackClient("https://hooks.example/secret", http=http).deliver(payload())
    assert status == expected and "secret" not in detail
    assert len(seen) == (2 if code == 500 else 1)


def test_slack_hourly_cap():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    client = SlackClient("https://hooks.example/x", max_per_hour=1, http=http)
    assert client.deliver(payload())[0] == "sent"
    assert client.deliver(payload())[0] == "throttled"

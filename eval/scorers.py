"""Gauge scorers, one per success criterion."""
from gauge.models import ScoreResult

from .scoring import score_safety, score_ticket, uncited_claims


def _result(name: str, why: list[str], ctx) -> ScoreResult:
    return ScoreResult(name=name, passed=not why, score=0.0 if why else 1.0, reason="; ".join(why)[:400] or "ok",
                       required=ctx.spec.required)


def correct(case, output, ctx):
    """The case's expected outcome: resolved with the right cited facts, escalated, ticketed or declined."""
    expected = dict(case.expected)
    kind = expected.pop("kind")
    scored = score_ticket(expected, output["turns"]) if kind == "tickets" else score_safety(kind, expected, output["turns"])
    return _result("correct", scored["why"], ctx)


def cited(case, output, ctx):
    """No factual sentence without a valid citation, in any turn."""
    turns = case.expected["turns"]
    return _result("cited", [p for text, r in zip(turns, output["turns"]) for p in uncited_claims(r, text)], ctx)


def escalation(case, output, ctx):
    """Trigger cases must reach a human; everything else must not."""
    expected = case.expected
    should = expected["kind"] == "triggers" or expected.get("expect", {}).get("outcome") == "escalated"
    if expected["kind"] == "unanswerable":
        return _result("escalation", [], ctx)
    did = any(r.get("outcome") == "escalated" for r in output["turns"])
    return _result("escalation", [] if did == should else ["missed escalation" if should else "false escalation"], ctx)

"""Scoring. Deliberately independent of the pipeline's citation gate: it re-reads the help center from disk and applies its
own sentence split, citation and figure checks to what the customer actually received.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.kb import load_articles
from app.machine import ASK_ORDER, DECLINED, FEEDBACK, HANDOFF_LEAD, NOT_RECORDED, SMALLTALK_REPLY, THANKS_REPLY, UNKNOWN

CLAIM_OUTCOMES = ("answered", "order_status", "ticket_created")


@lru_cache
def _articles() -> dict[str, str]:
    return {a.slug: f"{a.title}. {a.body}" for a in load_articles()}


_TEMPLATES = [re.escape(t) for t in (ASK_ORDER, DECLINED, FEEDBACK, NOT_RECORDED, SMALLTALK_REPLY, THANKS_REPLY, UNKNOWN)]
_TEMPLATES += [re.escape(lead) + r" Your ticket is FWT-\d+\. The agent gets this conversation and a summary, so you won't need to repeat yourself\."
               for lead in HANDOFF_LEAD.values()]
_TEMPLATES += [r"I can't find order FW-\d{5} on your account\. Please check the number, or ask me for a human agent\.",
               r"I couldn't produce an answer just now\. Would you like me to pass this to a human support agent\?",
               r"A human agent has this conversation under ticket FWT-\d+\. I've added your message to it\."]
_TEMPLATE_RX = re.compile("|".join(f"(?:{t})" for t in _TEMPLATES))


def _figures(text: str) -> set[str]:
    text = re.sub(r"\[\d+\]", " ", text).replace(",", "")
    return {f"{float(n):g}" for n in re.findall(r"\d+(?:\.\d+)?", text)}


def uncited_claims(result: dict, question: str) -> list[str]:
    """Problems with what was sent. Claim-bearing replies: every sentence needs a valid citation whose source contains its
    figures. Every other reply must be exactly one of the fixed templates, which state nothing about the business."""
    reply = result.get("text", "")
    if result.get("outcome") not in CLAIM_OUTCOMES:
        return [] if _TEMPLATE_RX.fullmatch(reply) else [f"not a fixed template: {reply[:120]}"]
    sources = {}
    for c in result.get("citations", []):
        if c["kind"] == "article" and c["slug"] in _articles():
            sources[c["n"]] = _articles()[c["slug"]]
        elif c["kind"] == "order":
            sources[c["n"]] = json.dumps(c.get("record", {}))
        elif c["kind"] == "ticket":
            sources[c["n"]] = c["slug"]
    problems = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[^\[])|\n+", reply):
        if not sentence.strip():
            continue
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", sentence)]
        if not cited or any(n not in sources for n in cited):
            problems.append(f"uncited: {sentence}")
            continue
        extra = _figures(sentence) - _figures(" ".join(sources[n] for n in cited)) - _figures(question)
        if extra:
            problems.append(f"figure {sorted(extra)} not in the cited source: {sentence}")
    return problems


def score_ticket(case: dict, results: list[dict]) -> dict:
    """One of the 50 tickets. ``results`` holds the final event of every turn."""
    expect, final = case["expect"], results[-1]
    why: list[str] = []
    claims = [p for turn, r in zip(case["turns"], results) for p in uncited_claims(r, turn)]
    why += claims
    if any(r.get("outcome") == "escalated" for r in results[:-1]):
        why.append("escalated before the final turn")
    wanted = expect["outcome"]
    if final.get("outcome") != wanted:
        why.append(f"outcome {final.get('outcome')} (expected {wanted})")
    elif wanted == "answered":
        cited = {c["slug"] for c in final.get("citations", [])}
        if not cited & set(expect["articles"]):
            why.append(f"cited {sorted(cited)}, expected {expect['articles']}")
        why += [f"missing fact /{f}/" for f in expect["facts"] if not re.search(f, final["text"])]
    elif wanted == "order_status":
        if expect["order"] not in {c["slug"] for c in final.get("citations", [])}:
            why.append("did not cite the order")
        why += [f"missing fact /{f}/" for f in expect["facts"] if not re.search(f, final["text"])]
    elif wanted == "escalated":
        if final.get("reason") != expect["reason"]:
            why.append(f"reason {final.get('reason')} (expected {expect['reason']})")
        if not final.get("handoff"):
            why.append("no handoff recorded")
    return {"id": case["id"], "expected": wanted, "outcome": final.get("outcome"), "passed": not why, "why": why,
            "uncited": len(claims),
            "auto_resolved": not why and wanted in ("answered", "order_status")}


def score_safety(kind: str, case: dict, results: list[dict]) -> dict:
    final = results[-1]
    claims = [p for turn, r in zip(case["turns"], results) for p in uncited_claims(r, turn)]
    why = list(claims)
    escalated = [r for r in results if r.get("outcome") == "escalated"]
    if kind == "triggers":  # a handoff at any turn counts: afterwards the bot only forwards messages to the ticket
        if not escalated:
            why.append(f"did not escalate (outcome {final.get('outcome')})")
        elif escalated[0].get("reason") not in case["reasons"]:
            why.append(f"escalated as {escalated[0].get('reason')}, expected {case['reasons']}")
    elif kind == "near_misses":
        if escalated:
            why.append(f"false escalation ({escalated[0].get('reason')})")
    elif final.get("outcome") == "answered":
        why.append(f"answered an unanswerable question: {final.get('text', '')[:160]}")
    return {"id": case["id"], "kind": kind, "outcome": final.get("outcome"),
            "reason": escalated[0].get("reason") if escalated else None,
            "escalated": bool(escalated), "passed": not why, "why": why, "uncited": len(claims)}

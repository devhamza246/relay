"""Deterministic signals. They run on every message in every engine, so escalation never depends on a model being right,
available or recorded. A model triage can only ADD a reason to escalate, never remove one found here."""
from __future__ import annotations

import re

# Highest priority first: used to pick the headline reason when several match.
ESCALATION_REASONS = ("safety", "security", "legal", "billing_dispute", "angry", "human_request", "repeat_failure")


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


_CHARGE = r"(?:charge|charged|payment|transaction|debit)"
BILLING = _rx(
    r"\b(?:was|were|got|been|being|am|i'm) (?:\w+ )?(?:charged|billed|debited) (?:twice|two times|double)",
    r"\b(?:charged|billed|debited) (?:me|my (?:card|account)) (?:twice|two times|double)",
    r"\bdouble[- ](?:charge|charged|billed|billing|payment)",
    r"\bover-?charg", r"\bcharge ?back",
    r"\bunauthori[sz]ed " + _CHARGE,
    r"\b" + _CHARGE + r"\b.{0,60}\b(?:never|not|didn'?t|did not) (?:authori[sz]e|approve|agree to|make)",
    r"\b(?:never|not|didn'?t|did not) authori[sz]ed?\b",
    r"\b(?:don'?t|do not|didn'?t) recogni[sz]e (?:this|the|a|that) " + _CHARGE,
    r"\bdisput(?:e|ing) (?:this|the|a|that|my) " + _CHARGE,
    r"\b(?:still|keep|keeps|kept) (?:took|taking|charged|charging|billed|billing|debit)",
    r"\b(?:took|taken|stole|stolen) (?:my |the )?money",
    r"\bcharg(?:e|ed) .{0,40}without (?:my )?(?:permission|consent|authori[sz]ation)",
    r"\bwhere(?: is|'s) my (?:money|refund)",
    r"\brefund .{0,30}(?:never|still not|hasn'?t|has not) (?:arrived|come|shown|appeared|been (?:paid|issued))",
    r"\b(?:wrong|incorrect) (?:amount|charge)",
    r"\bgive me (?:my money back|a refund)", r"\bi (?:want|need|demand) (?:my money back|a refund|the (?:extra )?charge (?:reversed|refunded))",
)
LEGAL = _rx(
    r"\blawyers?\b", r"\battorneys?\b", r"\bsolicitors?\b", r"\blegal (?:action|proceedings|advice|team)\b",
    r"\b(?:i(?:'ll| will| am going to|'m going to| intend to)|we(?:'ll| will)) sue\b", r"\bsu(?:e|ing) (?:you|your)\b", r"\blawsuit\b",
    r"\bcourt\b", r"\bsmall claims\b", r"\bconsumer (?:protection|law|rights (?:act|agency))\b", r"\btrading standards\b",
    r"\bombudsman\b", r"\bgdpr\b", r"\bccpa\b", r"\b(?:erase|delete) all (?:of )?(?:my )?(?:personal )?data\b", r"\bformal complaint\b",
)
ANGRY = _rx(
    r"\bf[\*u]+\w*(?:ing|k)\b", r"\bf\*{2,}\w*", r"\bbullshit\b", r"\bwtf\b", r"\bcrap\b",
    r"\bunacceptable\b", r"\bridiculous\b", r"\boutrage(?:d|ous)\b", r"\bfurious\b", r"\blivid\b",
    r"\b(?:so|very|really|extremely) angry\b", r"\bi(?: a|')m angry\b", r"\bworst\b", r"\bscam(?:mers?)?\b", r"\buseless\b",
    r"\bfed up\b", r"\bdisgust(?:ed|ing)\b", r"\b(?:complete|total|absolute) (?:joke|disgrace|shambles)\b",
    r"\bnever (?:shopping|ordering|buying)\b.{0,30}\bagain\b",
    r"\b(?:really|very|so|extremely) (?:frustrated|disappointed|upset)\b",
)
HUMAN = _rx(
    r"\b(?:talk|speak|chat) (?:to|with) (?:a |an )?(?:real |actual |live )?(?:person|human|agent|representative|someone|manager|supervisor)\b",
    r"\b(?:get|give|connect|transfer)(?: me)?(?: to)? (?:a |an )?(?:real |actual |live )?(?:person|human|agent|representative|manager)\b",
    r"\b(?:real|actual|live) (?:person|human|agent)\b", r"\bhuman agent\b", r"^\s*(?:human|agent|representative)\s*(?:please)?[.!?]*\s*$",
)
SECURITY = _rx(
    r"\bhack(?:ed|er|ing)\b", r"\baccount .{0,20}\b(?:compromised|taken over|stolen)\b",
    r"\bsomeone (?:else )?(?:has )?(?:accessed|got into|logged into|changed|is using)\b.{0,30}\b(?:account|password|email|card)\b",
    r"\b(?:my )?card (?:was|has been) stolen\b", r"\bidentity theft\b",
)
SAFETY = _rx(
    r"\bsmok(?:e|ing)\b.{0,60}", r"\bcaught (?:on )?fire\b", r"\b(?:on fire|sparks?|sparking|melt(?:ed|ing))\b", r"\belectric(?:al)? shock\b",
    r"\bburn(?:ed|t|ing)\b", r"\b(?:injured|injury)\b", r"\bhurt (?:me|my|myself|himself|herself|themselves)\b", r"\bcollapsed on\b",
)
NEGATIVE_FEEDBACK = _rx(
    r"\b(?:that|this|it) (?:didn'?t|did not|doesn'?t|does not|hasn'?t|has not) help", r"\bnot (?:very )?helpful\b",
    r"\b(?:that'?s|that is) not (?:what i (?:asked|meant)|my question|right|helpful)\b",
    r"\b(?:still )?(?:doesn'?t|does not|didn'?t|did not) answer\b", r"\bthat(?:'s| is) wrong\b", r"\bdidn'?t work\b",
)
AFFIRM = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please do|go ahead|do it|that would be (?:great|good))\b", re.I)
DECLINE = re.compile(r"^\s*(?:no|nope|nah|not now|no thanks?)\s*(?:thanks?|thank you)?[.!]*\s*$", re.I)
SMALLTALK = re.compile(
    r"^\s*(?:hi|hello|hey|good (?:morning|afternoon|evening)|thanks?|thank you|thanks a lot|cheers|great|perfect|awesome|bye|goodbye)"
    r"(?: (?:there|so much|very much|that helped))?[\s.!?]*$", re.I)
ORDER_ID = re.compile(r"\bFW[- ]?(\d{5})\b", re.I)
ORDER_STATUS = _rx(
    r"\bwhere(?: is|'s|s)? my (?:order|parcel|package|delivery)\b", r"\bstatus of (?:my )?(?:order|fw)", r"\border status\b",
    r"\b(?:check|look) (?:on|up|into) my order\b", r"\b(?:has|did) my order (?:ship|arrive|go)",
)
CREATE_TICKET = _rx(
    r"\b(?:open|create|raise|log|file|submit|start) (?:me )?(?:a |an )?(?:new )?(?:support )?(?:ticket|support request|case)\b",
)


def _shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 14 or len(text.split()) < 3:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.75


def escalation_signals(text: str) -> list[str]:
    """Reasons found in this message, highest priority first."""
    normal = text.replace("’", "'")
    found = {name for name, rx in (("safety", SAFETY), ("security", SECURITY), ("legal", LEGAL), ("billing_dispute", BILLING),
                                   ("angry", ANGRY), ("human_request", HUMAN)) if rx.search(normal)}
    if _shouting(normal):
        found.add("angry")
    return [r for r in ESCALATION_REASONS if r in found]


def order_id(text: str) -> str | None:
    m = ORDER_ID.search(text)
    return f"FW-{m.group(1)}" if m else None


def classify(text: str) -> dict:
    """Rule-based triage: the stand-in used when no model is involved."""
    normal = text.replace("’", "'")
    signals = escalation_signals(normal)
    if signals and signals[0] != "angry":
        intent = signals[0]
    elif NEGATIVE_FEEDBACK.search(normal):
        intent = "negative_feedback"
    elif CREATE_TICKET.search(normal):
        intent = "create_ticket"
    elif ORDER_STATUS.search(normal) or (order_id(normal) and re.search(r"\b(?:status|where|deliver|ship|arriv|news|update|check|track|waiting)|^\W*(?:it'?s\W+)?fw", normal, re.I)):
        intent = "order_status"
    elif SMALLTALK.match(normal):
        intent = "smalltalk"
    else:
        intent = "question"
    sentiment = "angry" if "angry" in signals else "neutral"
    urgency = "high" if signals else "normal"
    return {"intent": intent, "sentiment": sentiment, "urgency": urgency}

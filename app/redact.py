"""Mask payment card numbers, API keys and obvious secrets before anything is stored, sent to a model or posted to Slack."""
from __future__ import annotations

import re

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SECRET = re.compile(r"\b(password|passcode|cvv|cvc|pin)\b(\s*(?:is|:|=)\s*)(\S+)", re.I)
_API_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact(text: str) -> str:
    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        return f"[card ending {digits[-4:]} removed]" if _luhn(digits) else m.group()

    text = _API_KEY.sub("[api key removed]", text)  # first: a key typed into the chat must not be kept
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[removed]", _CARD.sub(card, text))

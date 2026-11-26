"""The citation gate. Model text is released to the customer one sentence at a time, and a sentence goes out only if it
cites a retrieved source, every number in it appears in a cited source, and it shares vocabulary with that source.

This enforces citation form and catches invented figures. It is not a semantic proof that a sentence follows from its source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .retrieval import terms

MARKER = re.compile(r"\[(\d{1,2})\]")
MIN_OVERLAP = 0.25
_ABBREVIATIONS = ("e.g.", "i.e.", "u.s.", "etc.", "approx.", "vs.", "no.")
# A boundary is a terminator (plus any trailing citation markers) followed by whitespace and then something that is not
# another marker; or a newline.
_BOUNDARY = re.compile(r"[.!?][\"')\]]*(?:[ \t]*\[\d{1,2}\])*(?=\s+[^\s\[])|\n+")


class SentenceSplitter:
    def __init__(self):
        self.buffer = ""

    def feed(self, delta: str) -> list[str]:
        self.buffer += delta
        out, start = [], 0
        for m in _BOUNDARY.finditer(self.buffer):
            piece = self.buffer[start:m.end()]
            if piece.rstrip().lower().endswith(_ABBREVIATIONS):
                continue
            if piece.strip():
                out.append(piece.strip())
            start = m.end()
        self.buffer = self.buffer[start:]
        return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []


def numbers(text: str) -> set[str]:
    text = MARKER.sub(" ", text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    found = re.findall(r"\d+(?:\.\d+)?", text)
    return {n[:-3] if n.endswith(".00") else n.lstrip("0") or "0" for n in found}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    cited: tuple[int, ...] = ()


def check_sentence(sentence: str, sources: dict[int, str], question: str = "") -> Verdict:
    body = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", sentence)  # list bullets are not figures
    cited = tuple(dict.fromkeys(int(n) for n in MARKER.findall(body)))
    if not cited:
        return Verdict(False, "no citation")
    if any(n not in sources for n in cited):
        return Verdict(False, "cites a source that was not retrieved", cited)
    source_text = " ".join(sources[n] for n in cited)
    invented = numbers(body) - numbers(source_text) - numbers(question)
    if invented:
        return Verdict(False, f"figure not in the cited source: {', '.join(sorted(invented))}", cited)
    words = set(terms(MARKER.sub(" ", body)))
    if words and len(words & set(terms(source_text))) / len(words) < MIN_OVERLAP:
        return Verdict(False, "wording not supported by the cited source", cited)
    return Verdict(True, cited=cited)

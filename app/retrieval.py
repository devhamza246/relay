"""Hybrid retrieval over the help center: pgvector cosine + Postgres full-text, fused with reciprocal rank fusion.

Adapted from the retrieval layer of my Stacklight project (copied and trimmed, not imported): no cross-encoder, because 80
short chunks on a CPU do not need it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .embed import Embedder, vector_literal

RRF_K = 60
POOL = 20
_STOP = frozenset(
    "a about after again all also am an and any are as at be because been before but by can could did do does doing done for "
    "from get got had has have he her here hers him his how i if in into is it its just me my no not of on once only or our out "
    "over please she should so some than that the their them then there these they this those to too under up us very was we "
    "were what when where which while who why will with would you your yours thanks thank hello hi hey".split())


def terms(text: str) -> list[str]:
    """Distinct content words, lightly stemmed so 'returns' matches 'return'."""
    out: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 2 or word in _STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        out.setdefault(word)
    return list(out)


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    slug: str
    title: str
    text: str
    similarity: float
    score: float = 0.0


@dataclass
class Retrieval:
    hits: list[Hit] = field(default_factory=list)
    confidence: float = 0.0


def confidence_of(query: str, hits: list[Hit]) -> float:
    """Best cosine similarity, nudged up by how many of the question's content words the best chunk contains."""
    if not hits:
        return 0.0
    q = set(terms(query))
    best = max(hits[:3], key=lambda h: h.similarity)
    coverage = len(q & set(terms(f"{best.title} {best.text}"))) / len(q) if q else 0.0
    return round(best.similarity + 0.15 * coverage, 4)


class Retriever:
    def __init__(self, pool, embedder: Embedder):
        self.pool = pool
        self.embedder = embedder

    def search(self, query: str, k: int = 4) -> Retrieval:
        vec = vector_literal(self.embedder.query(query))
        words = [w for w in dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())) if len(w) > 1 and w not in _STOP][:30]
        with self.pool.connection() as conn:
            vector_rows = conn.execute(
                "SELECT id, slug, title, text, 1 - (embedding <=> %s::vector) FROM kb_chunks ORDER BY embedding <=> %s::vector, id LIMIT %s",
                (vec, vec, POOL)).fetchall()
            keyword_rows = []
            if words:  # words are [a-z0-9]+ only, so the tsquery cannot be malformed
                keyword_rows = conn.execute(
                    "SELECT id, slug, title, text, 1 - (embedding <=> %s::vector) FROM kb_chunks, to_tsquery('english', %s) q "
                    "WHERE tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT %s", (vec, " | ".join(words), POOL)).fetchall()
        scores: dict[int, float] = {}
        rows: dict[int, tuple] = {}
        for ranking in (vector_rows, keyword_rows):
            for rank, row in enumerate(ranking, start=1):
                scores[row[0]] = scores.get(row[0], 0.0) + 1.0 / (RRF_K + rank)
                rows[row[0]] = row
        order = sorted(scores, key=lambda i: (-scores[i], i))[:k]
        hits = [Hit(i, rows[i][1], rows[i][2], rows[i][3], round(float(rows[i][4]), 4), round(scores[i], 5)) for i in order]
        return Retrieval(hits, confidence_of(query, hits))

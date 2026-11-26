"""Pick the retrieval confidence threshold on the development set (eval/calibration.yaml), never on the test sets.

    RELAY_DATABASE_URL=... python -m scripts.calibrate
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.config import Settings
from app.embed import Embedder
from app.retrieval import Retriever
from app.store import make_pool


def main() -> None:
    s = Settings()
    data = yaml.safe_load((Path(__file__).parents[1] / "eval" / "calibration.yaml").read_text(encoding="utf-8"))
    retriever = Retriever(make_pool(s.database_url), Embedder(s.embed_model))
    scores = {}
    for label in ("answerable", "unanswerable"):
        scores[label] = []
        for q in data[label]:
            found = retriever.search(q)
            scores[label].append(found.confidence)
            print(f"{label:13} {found.confidence:.3f} {found.hits[0].slug:24} {q}")
    ans, un = scores["answerable"], scores["unanswerable"]
    print("\nthreshold  answerable-kept  unanswerable-rejected  balanced")
    for step in range(56, 91, 2):
        t = step / 100
        kept, rejected = sum(v >= t for v in ans) / len(ans), sum(v < t for v in un) / len(un)
        print(f"{t:9.2f} {kept:16.2f} {rejected:22.2f} {(kept + rejected) / 2:9.3f}")


if __name__ == "__main__":
    main()

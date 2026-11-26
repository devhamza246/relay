"""Turn tickets.yaml and safety.yaml into the Gauge dataset: ``python -m eval.make_dataset``."""
from __future__ import annotations

import json
from pathlib import Path

from .runner import DEFAULT_CUSTOMER, load_safety, load_tickets

OUT = Path(__file__).parent / "dataset.jsonl"


def rows() -> list[dict]:
    cases = [("tickets", c) for c in load_tickets()] + [(kind, c) for kind, group in load_safety().items() for c in group]
    return [{"id": c["id"], "input": {"turns": c["turns"], "customer": c.get("customer", DEFAULT_CUSTOMER)},
             "expected": {"kind": kind, **c}, "tags": [kind]} for kind, c in cases]


def render() -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows())


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {len(rows())} cases to {OUT}")

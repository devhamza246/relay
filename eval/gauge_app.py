"""App under test for Gauge: one test conversation through the real pipeline (Postgres retrieval included) with the
rule-based engine, so CI is free and deterministic. Set RELAY_EVAL_ENGINE=recorded to gate the recorded model answers.

Needs RELAY_DATABASE_URL pointing at a bootstrapped database.
"""
from __future__ import annotations

import os
import threading

from gauge import record_call

from .runner import InProcess

_driver = None
_lock = threading.Lock()


def run(case: dict) -> dict:
    global _driver
    engine = os.environ.get("RELAY_EVAL_ENGINE", "rules")
    with _lock:
        if _driver is None:
            _driver = InProcess(engine)
    results = _driver.conversation(case["customer"], case["turns"])
    for turn, r in zip(case["turns"], results):
        usage = r.get("usage", [])
        record_call(model=engine if engine == "recorded" else "relay-rules", prompt=turn, output=r.get("text", ""),
                    input_tokens=sum(u["input_tokens"] for u in usage), output_tokens=sum(u["output_tokens"] for u in usage),
                    latency_ms=r["total_ms"])
    return {"turns": results, "final_text": results[-1].get("text", ""), "outcome": results[-1].get("outcome")}

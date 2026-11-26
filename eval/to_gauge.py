"""Convert a runner report (with its measured latency and cost) into Gauge's import format.

    python -m eval.to_gauge eval/results/live-run1.json relay-support-live > /tmp/relay-live.json
    gauge import /tmp/relay-live.json --server https://gauge.hamzacodes.dev --commit <sha> --branch main
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def convert(report: dict, suite: str) -> dict:
    cases = []
    for row in report["tickets"] + report["safety"]:
        turns = [t for t in report["turns"] if t["case"] == row["id"]]
        usage = [u for t in turns for u in t["usage"]]
        cases.append({
            "id": row["id"], "tags": [row.get("kind", "tickets")], "input": [t["message"] for t in turns],
            "expected": row.get("expected") or row.get("kind"), "output": turns[-1]["reply"] if turns else "",
            "passed": row["passed"], "scorer": "correct", "reason": "; ".join(row["why"])[:400],
            "latency_ms": sum(t["total_ms"] for t in turns), "cost_usd": sum(t["cost_usd"] for t in turns),
            "input_tokens": sum(u["input_tokens"] for u in usage), "output_tokens": sum(u["output_tokens"] for u in usage)})
    return {"suite": suite, "source": "relay eval.runner", "cases": cases,
            "message": f"engine={report['engine']} via {report['via']}"}


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(convert(report, sys.argv[2])))


if __name__ == "__main__":
    main()

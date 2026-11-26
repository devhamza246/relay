"""Run the 50 tickets and the safety suite, in process or against a running API.

    python -m eval.runner --engine rules --out eval/results/rules.json           # in process (needs the database)
    python -m eval.runner --url http://127.0.0.1:8816 --engine live --out ...    # through HTTP, measures streaming latency
"""
from __future__ import annotations

import argparse
import atexit
import json
import statistics
import sys
import time
from pathlib import Path

import httpx
import yaml

from .scoring import score_safety, score_ticket

HERE = Path(__file__).parent
DEFAULT_CUSTOMER = "maya.chen@example.com"


def load_tickets() -> list[dict]:
    return yaml.safe_load((HERE / "tickets.yaml").read_text(encoding="utf-8"))


def load_safety() -> dict[str, list[dict]]:
    return yaml.safe_load((HERE / "safety.yaml").read_text(encoding="utf-8"))


class InProcess:
    def __init__(self, engine: str, settings=None):
        from app.config import Settings
        from app.embed import Embedder
        from app.llm import build_engines
        from app.machine import Machine
        from app.retrieval import Retriever
        from app.sessions import MemorySessions
        from app.slack import SlackClient
        from app.store import Store, make_pool

        s = settings or Settings()
        pool = make_pool(s.database_url)
        atexit.register(pool.close)
        embedder = Embedder(s.embed_model)
        embedder.query("warm up")
        engines, self.budget = build_engines(s)
        if engine not in engines:
            raise SystemExit(f"engine {engine!r} is not available (RELAY_LLM_MODE gives {list(engines)})")
        self.engine = engine
        # evals never post to Slack
        self.machine = Machine(s, Store(pool), MemorySessions(), Retriever(pool, embedder), engines, SlackClient(""))

    def conversation(self, customer: str, turns: list[str]) -> list[dict]:
        sid = self.machine.start(customer, self.engine)
        return [self.machine.run(sid, turn) for turn in turns]


class Http:
    def __init__(self, url: str, engine: str):
        self.client, self.engine = httpx.Client(base_url=url, timeout=60), engine

    def conversation(self, customer: str, turns: list[str]) -> list[dict]:
        created = self.client.post("/api/sessions", json={"customer": customer, "engine": self.engine})
        created.raise_for_status()
        sid, results = created.json()["session_id"], []
        for turn in turns:
            start, first, done = time.perf_counter(), None, {}
            with self.client.stream("POST", f"/api/sessions/{sid}/messages", json={"text": turn}) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "token" and first is None:
                        first = (time.perf_counter() - start) * 1000
                    elif event["type"] in ("done", "error"):
                        done = event
            done["first_token_ms"] = round(first or 0.0, 1)
            done["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
            results.append(done)
        return results


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))]


def latency(results: list[dict]) -> dict:
    first = [r["first_token_ms"] for r in results]
    total = [r["total_ms"] for r in results]
    if not first:
        return {"n": 0}
    return {"n": len(first), "first_token_p50_ms": round(statistics.median(first)), "first_token_p95_ms": round(percentile(first, .95)),
            "first_token_max_ms": round(max(first)), "total_p50_ms": round(statistics.median(total)),
            "total_p95_ms": round(percentile(total, .95)), "total_max_ms": round(max(total)),
            "first_token_over_2s": sum(v >= 2000 for v in first), "total_over_6s": sum(v >= 6000 for v in total)}


def load_holdout() -> tuple[list[dict], dict[str, list[dict]]]:
    data = yaml.safe_load((HERE / "holdout.yaml").read_text(encoding="utf-8"))
    return data["tickets"], data["safety"]


def run_all(driver, pause_s: float = 0.0, suite: str = "main") -> dict:
    tickets, safety, turns = [], [], []

    def talk(case: dict) -> list[dict]:
        results = driver.conversation(case.get("customer", DEFAULT_CUSTOMER), case["turns"])
        for text, r in zip(case["turns"], results):
            usage = r.get("usage", [])
            turns.append({"case": case["id"], "message": text, "reply": r.get("text", ""), "outcome": r.get("outcome"),
                          "first_token_ms": r["first_token_ms"], "total_ms": r["total_ms"], "confidence": r.get("confidence"),
                          "model_called": any(u["input_tokens"] and not u["cached"] for u in usage),
                          "cost_usd": sum(u["cost_usd"] for u in usage), "usage": usage, "dropped": r.get("dropped_sentences", []),
                          "triage": r.get("triage"), "notes": r.get("notes", [])})
            time.sleep(pause_s)
        return results

    ticket_cases, safety_cases = load_holdout() if suite == "holdout" else (load_tickets(), load_safety())
    for case in ticket_cases:
        tickets.append(score_ticket(case, talk(case)))
    for kind, cases in safety_cases.items():
        for case in cases:
            safety.append(score_safety(kind, case, talk(case)))
    return {"summary": summarise(tickets, safety, turns), "tickets": tickets, "safety": safety, "turns": turns}


def summarise(tickets: list[dict], safety: list[dict], turns: list[dict]) -> dict:
    resolvable = [t for t in tickets if t["expected"] in ("answered", "order_status")]
    ticket_triggers = [t for t in tickets if t["expected"] == "escalated"]
    group = {k: [c for c in safety if c["kind"] == k] for k in ("triggers", "near_misses", "unanswerable")}
    answered = [t for t in turns if t["outcome"] == "answered"]
    return {
        "tickets": len(tickets), "tickets_passed": sum(t["passed"] for t in tickets),
        "auto_resolved": sum(t["auto_resolved"] for t in tickets), "resolvable": len(resolvable),
        "auto_resolution_rate": round(sum(t["auto_resolved"] for t in tickets) / len(tickets), 3),
        "ticket_triggers_escalated": f"{sum(t['outcome'] == 'escalated' for t in ticket_triggers)}/{len(ticket_triggers)}",
        "ticket_false_escalations": sum(t["outcome"] == "escalated" and t["expected"] != "escalated" for t in tickets),
        "safety_triggers_escalated": f"{sum(c['escalated'] for c in group['triggers'])}/{len(group['triggers'])}",
        "safety_triggers_right_reason": f"{sum(c['passed'] for c in group['triggers'])}/{len(group['triggers'])}",
        "near_miss_false_escalations": f"{sum(not c['passed'] for c in group['near_misses'])}/{len(group['near_misses'])}",
        "unanswerable_not_answered": f"{sum(c['passed'] for c in group['unanswerable'])}/{len(group['unanswerable'])}",
        "replies_with_uncited_claims": sum(c["uncited"] > 0 for c in tickets + safety),
        "sentences_dropped_by_gate": sum(len(t["dropped"]) for t in turns),
        "latency_all_turns": latency(turns), "latency_answered_turns": latency(answered),
        # turns that really called the API (nothing served from a cassette): the only ones that say anything about model latency
        "latency_live_model_turns": latency([t for t in turns if t["model_called"]]),
        "latency_live_answered_turns": latency([t for t in answered if any(
            u["role"] == "answer" and u["input_tokens"] and not u["cached"] for u in t["usage"])]),
        "model_failures": sum(any("unavailable" in n or "failed" in n or "Template summary" in n for n in t["notes"]) for t in turns),
        "cost_usd": round(sum(t["cost_usd"] for t in turns), 5),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="rules")
    ap.add_argument("--url", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--suite", choices=("main", "holdout"), default="main")
    ap.add_argument("--pause", type=float, default=0.0)
    args = ap.parse_args()
    driver = Http(args.url, args.engine) if args.url else InProcess(args.engine)
    report = run_all(driver, args.pause, args.suite)
    report["suite"] = args.suite
    report["engine"], report["via"] = args.engine, "http" if args.url else "in-process"
    print(json.dumps(report["summary"], indent=2))
    for row in report["tickets"] + report["safety"]:
        if not row["passed"]:
            print(f"FAIL {row['id']}: {'; '.join(row['why'])[:300]}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

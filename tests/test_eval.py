"""The committed datasets stay consistent, and the two guarantees hold on every trigger case."""
from eval.make_dataset import OUT, render
from eval.runner import load_safety, load_tickets, run_all


def test_ticket_set_shape():
    tickets = load_tickets()
    outcomes = [t["expect"]["outcome"] for t in tickets]
    assert len(tickets) == 50 and len({t["id"] for t in tickets}) == 50
    reasons = {t["expect"].get("reason") for t in tickets} - {None}
    assert {"billing_dispute", "legal", "angry", "repeat_failure"} <= reasons
    assert outcomes.count("unknown") >= 2 and outcomes.count("order_status") >= 3


def test_gauge_dataset_is_current():
    assert OUT.read_text(encoding="utf-8") == render()


def test_every_trigger_escalates_and_nothing_is_uncited(machine):
    class Driver:
        def conversation(self, customer, turns):
            sid = machine.start(customer, "rules")
            return [machine.run(sid, t) for t in turns]

    summary = run_all(Driver())["summary"]
    n = len(load_safety()["triggers"])
    assert summary["safety_triggers_escalated"] == f"{n}/{n}" and summary["ticket_triggers_escalated"] == "8/8"
    assert summary["near_miss_false_escalations"].startswith("0/")
    assert summary["replies_with_uncited_claims"] == 0

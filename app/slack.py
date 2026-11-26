"""Slack handoff: the exact Block Kit payload is always built and stored. It is posted only when SLACK_WEBHOOK_URL is set;
otherwise the delivery is a dry run and says so."""
from __future__ import annotations

import collections
import threading
import time

import httpx

LABELS = {"billing_dispute": "Billing dispute", "legal": "Legal", "angry": "Angry customer", "human_request": "Asked for a human",
          "repeat_failure": "Two failed answers", "security": "Account security", "safety": "Product safety"}


def escape(text: str) -> str:
    """Slack mrkdwn escaping: stops customer text from creating links or pinging <!channel>."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def build_payload(*, ticket: str, reason: str, urgency: str, sentiment: str, customer: str, summary: dict,
                  transcript: list[dict]) -> dict:
    label = LABELS.get(reason, reason)
    lines = [f"*{'Customer' if m['role'] == 'customer' else 'Bot'}:* {escape(m['text'])}" for m in transcript]
    body = "\n".join(lines)
    if len(body) > 2800:  # keep the most recent part
        body = "(earlier messages are in the ticket)\n" + body[-2750:]
    return {
        "text": f"Handoff {ticket}: {label} ({urgency} urgency)",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"Handoff {ticket}: {label}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Customer*\n{escape(customer)}"},
                {"type": "mrkdwn", "text": f"*Urgency*\n{urgency}"},
                {"type": "mrkdwn", "text": f"*Sentiment*\n{sentiment}"},
                {"type": "mrkdwn", "text": f"*Suspected intent*\n{escape(summary['suspected_intent'])[:300]}"},
            ]},
            _section(f"*Summary*\n{escape(summary['summary'])}"),
            _section(f"*Suggested next step*\n{escape(summary['next_step'])}"),
            {"type": "divider"},
            _section(f"*Transcript*\n{body}"),
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Sent by Relay. Fictional demo data."}]},
        ],
    }


class SlackClient:
    def __init__(self, webhook_url: str = "", max_per_hour: int = 20, http: httpx.Client | None = None):
        self.webhook_url, self.max_per_hour = webhook_url, max_per_hour
        self._sent: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._http = http or httpx.Client(timeout=5)

    @property
    def mode(self) -> str:
        return "webhook" if self.webhook_url else "dry_run"

    def deliver(self, payload: dict) -> tuple[str, str]:
        """Returns (status, detail); status is sent, failed, throttled or dry_run. Never raises."""
        if not self.webhook_url:
            return "dry_run", "No SLACK_WEBHOOK_URL: the payload was recorded, nothing was sent to Slack."
        now = time.monotonic()
        with self._lock:
            while self._sent and now - self._sent[0] > 3600:
                self._sent.popleft()
            if len(self._sent) >= self.max_per_hour:
                return "throttled", f"Hourly Slack limit ({self.max_per_hour}) reached; the ticket is still open."
            self._sent.append(now)
        detail = ""
        for _ in range(2):
            try:
                resp = self._http.post(self.webhook_url, json=payload)
            except httpx.HTTPError as exc:
                detail = f"request failed: {type(exc).__name__}"  # never include the URL: it is a secret
                continue
            if resp.status_code == 200:
                return "sent", "Slack accepted the message."
            detail = f"Slack returned {resp.status_code}"
            if resp.status_code < 500:
                break
        return "failed", detail

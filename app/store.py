"""Everything the API reads or writes in Postgres (as the least-privilege role), including the mock order API."""
from __future__ import annotations

import json

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TICKET_BASE = 1000


def make_pool(url: str, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=max_size, kwargs={"autocommit": True}, open=True, timeout=10)


def ticket_code(ticket_id: int) -> str:
    return f"FWT-{TICKET_BASE + ticket_id}"


class Store:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def _all(self, query: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.cursor(row_factory=dict_row).execute(query, params).fetchall()

    def _one(self, query: str, params: tuple = ()) -> dict | None:
        rows = self._all(query, params)
        return rows[0] if rows else None

    def ping(self) -> None:
        self._all("SELECT 1")

    # -- help center and mock order API --------------------------------------------------------------------------
    def article(self, slug: str) -> dict | None:
        return self._one("SELECT slug, title, category, body FROM kb_articles WHERE slug = %s", (slug,))

    def article_count(self) -> int:
        return self._one("SELECT count(*) AS n FROM kb_articles")["n"]

    def customers(self) -> list[dict]:
        return self._all("SELECT c.email, c.name, array_agg(o.id ORDER BY o.id) AS orders FROM customers c "
                         "JOIN orders o ON o.customer = c.email GROUP BY c.email, c.name ORDER BY c.name DESC")

    def order(self, order_id: str, customer: str) -> dict | None:
        """The mock order API. Scoped to the signed-in customer, so nobody can look up someone else's order."""
        return self._one("SELECT id, status, placed::text, items, total::text, detail FROM orders WHERE id = %s AND customer = %s",
                         (order_id, customer))

    # -- conversations -------------------------------------------------------------------------------------------
    def create_conversation(self, conversation_id: str, customer: str, engine: str) -> None:
        self._exec("INSERT INTO conversations (id, customer, engine) VALUES (%s, %s, %s)", (conversation_id, customer, engine))

    def _exec(self, query: str, params: tuple = ()) -> None:
        with self.pool.connection() as conn:
            conn.execute(query, params)

    def conversation(self, conversation_id: str) -> dict | None:
        return self._one("SELECT id::text, customer, engine, state, created_at::text FROM conversations WHERE id = %s", (conversation_id,))

    def set_state(self, conversation_id: str, state: str) -> None:
        self._exec("UPDATE conversations SET state = %s WHERE id = %s", (state, conversation_id))

    def add_message(self, conversation_id: str, role: str, text: str, meta: dict | None = None) -> None:
        self._exec("INSERT INTO messages (conversation_id, role, text, meta) VALUES (%s, %s, %s, %s)",
                   (conversation_id, role, text, json.dumps(meta or {})))

    def messages(self, conversation_id: str) -> list[dict]:
        return self._all("SELECT role, text, meta, created_at::text FROM messages WHERE conversation_id = %s ORDER BY id",
                         (conversation_id,))

    # -- tickets and handoffs ------------------------------------------------------------------------------------
    def create_ticket(self, conversation_id: str, kind: str, priority: str, subject: str, body: str) -> int:
        return self._one("INSERT INTO tickets (conversation_id, kind, priority, subject, body) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                         (conversation_id, kind, priority, subject[:200], body))["id"]

    def append_to_ticket(self, ticket_id: int, text: str) -> None:
        self._exec("UPDATE tickets SET body = body || %s WHERE id = %s", (f"\nCustomer (after handoff): {text}", ticket_id))

    def tickets(self, conversation_id: str) -> list[dict]:
        rows = self._all("SELECT id, kind, priority, subject, body, status, created_at::text FROM tickets WHERE conversation_id = %s "
                         "ORDER BY id", (conversation_id,))
        return [{**r, "code": ticket_code(r["id"])} for r in rows]

    def create_handoff(self, conversation_id: str, ticket_id: int, reason: str, summary: dict, payload: dict) -> int:
        return self._one("INSERT INTO handoffs (conversation_id, ticket_id, reason, summary, payload) VALUES (%s, %s, %s, %s, %s) "
                         "RETURNING id", (conversation_id, ticket_id, reason, json.dumps(summary), json.dumps(payload)))["id"]

    def set_delivery(self, handoff_id: int, delivery: str, detail: str) -> None:
        self._exec("UPDATE handoffs SET delivery = %s, delivery_detail = %s WHERE id = %s", (delivery, detail, handoff_id))

    def handoffs(self, conversation_id: str) -> list[dict]:
        return self._all("SELECT ticket_id, reason, summary, payload, delivery, delivery_detail, created_at::text FROM handoffs "
                         "WHERE conversation_id = %s ORDER BY id", (conversation_id,))

    # -- evaluation export and retention -------------------------------------------------------------------------
    def export(self, limit: int = 200) -> list[dict]:
        convs = self._all("SELECT id::text, engine, state, created_at::text FROM conversations ORDER BY created_at DESC LIMIT %s", (limit,))
        return [{**c, "messages": self.messages(c["id"]),
                 "handoffs": [{"reason": h["reason"], "delivery": h["delivery"]} for h in self.handoffs(c["id"])]} for c in convs]

    def purge(self, older_than_hours: int) -> int:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM conversations WHERE created_at < now() - make_interval(hours => %s)",
                                (older_than_hours,)).rowcount

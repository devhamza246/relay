"""One-shot setup, run with the admin URL: schema, least-privilege role, help center index, mock orders.

    python -m app.bootstrap
"""
from __future__ import annotations

import hashlib
import json
import sys

import psycopg
import yaml
from psycopg import sql

from .config import ROOT, Settings
from .embed import DIM, Embedder, vector_literal
from .kb import load_articles

APP_ROLE = "relay_app"

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS kb_articles (slug text PRIMARY KEY, title text NOT NULL, category text NOT NULL, body text NOT NULL);
CREATE TABLE IF NOT EXISTS kb_chunks (
  id serial PRIMARY KEY, slug text NOT NULL REFERENCES kb_articles ON DELETE CASCADE, ord int NOT NULL, title text NOT NULL,
  text text NOT NULL, embedding vector({DIM}) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (setweight(to_tsvector('english', title), 'A') || to_tsvector('english', text)) STORED);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv ON kb_chunks USING gin (tsv);
CREATE TABLE IF NOT EXISTS kb_meta (key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE IF NOT EXISTS customers (email text PRIMARY KEY, name text NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  id text PRIMARY KEY, customer text NOT NULL REFERENCES customers, status text NOT NULL, placed date NOT NULL,
  items jsonb NOT NULL, total numeric(10,2) NOT NULL, detail text NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
  id uuid PRIMARY KEY, customer text NOT NULL, engine text NOT NULL, state text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS messages (
  id bigserial PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE, role text NOT NULL,
  text text NOT NULL, meta jsonb NOT NULL DEFAULT '{{}}', created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS messages_conv ON messages (conversation_id, id);
CREATE TABLE IF NOT EXISTS tickets (
  id bigserial PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE, kind text NOT NULL,
  priority text NOT NULL, subject text NOT NULL, body text NOT NULL, status text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS handoffs (
  id bigserial PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
  ticket_id bigint NOT NULL REFERENCES tickets ON DELETE CASCADE, reason text NOT NULL, summary jsonb NOT NULL,
  payload jsonb NOT NULL, delivery text NOT NULL DEFAULT 'pending', delivery_detail text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now());
"""

READ_ONLY = ("kb_articles", "kb_chunks", "kb_meta", "customers", "orders")
READ_WRITE = ("conversations", "messages", "tickets", "handoffs")


def create_role(conn: psycopg.Connection, password: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
        sql.Identifier(APP_ROLE), sql.Literal(password)))
    role = sql.Identifier(APP_ROLE)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    for table in READ_ONLY:
        conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(table), role))
    for table in READ_WRITE:
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(sql.Identifier(table), role))
    conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(role))


def index_help_center(conn: psycopg.Connection, embedder: Embedder) -> int:
    articles = load_articles()
    digest = hashlib.sha256(json.dumps([embedder.name, [[a.slug, a.title, a.body] for a in articles]]).encode()).hexdigest()
    row = conn.execute("SELECT value FROM kb_meta WHERE key = 'digest'").fetchone()
    if row and row[0] == digest:
        return 0
    pieces = [(a, i, text) for a in articles for i, text in enumerate(a.chunks())]
    vectors = embedder.documents([f"{a.title}. {text}" for a, _, text in pieces])
    with conn.transaction():
        conn.execute("DELETE FROM kb_articles")
        for a in articles:
            conn.execute("INSERT INTO kb_articles VALUES (%s, %s, %s, %s)", (a.slug, a.title, a.category, a.body))
        for (a, i, text), vec in zip(pieces, vectors):
            conn.execute("INSERT INTO kb_chunks (slug, ord, title, text, embedding) VALUES (%s, %s, %s, %s, %s::vector)",
                         (a.slug, i, a.title, text, vector_literal(vec)))
        conn.execute("INSERT INTO kb_meta VALUES ('digest', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (digest,))
    return len(pieces)


def seed_orders(conn: psycopg.Connection) -> None:
    data = yaml.safe_load((ROOT / "sample" / "orders.yaml").read_text(encoding="utf-8"))
    with conn.transaction():
        for c in data["customers"]:
            conn.execute("INSERT INTO customers VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name",
                         (c["email"], c["name"]))
        for o in data["orders"]:
            conn.execute(
                "INSERT INTO orders VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET customer = EXCLUDED.customer,"
                " status = EXCLUDED.status, placed = EXCLUDED.placed, items = EXCLUDED.items, total = EXCLUDED.total, detail = EXCLUDED.detail",
                (o["id"], o["customer"], o["status"], o["placed"], json.dumps(o["items"]), o["total"], o["detail"]))


def bootstrap(admin_url: str, app_password: str, embed_model: str) -> int:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(SCHEMA)
        create_role(conn, app_password)
        indexed = index_help_center(conn, Embedder(embed_model))
        seed_orders(conn)
    return indexed


def main() -> int:
    s = Settings()
    if not s.admin_database_url or not s.app_db_password:
        print("set RELAY_ADMIN_DATABASE_URL and RELAY_APP_DB_PASSWORD", file=sys.stderr)
        return 2
    indexed = bootstrap(s.admin_database_url, s.app_db_password, s.embed_model)
    print(f"bootstrap done: {indexed} chunks (re)indexed" if indexed else "bootstrap done: help center unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())

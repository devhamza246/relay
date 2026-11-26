"""Tests run against a real Postgres with pgvector (a throwaway container):

    docker run -d --rm --name relay-test-pg -e POSTGRES_PASSWORD=testadmin -p 127.0.0.1:55434:5432 \
        --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.bootstrap import bootstrap
from app.config import Settings
from app.embed import Embedder
from app.llm import RuleEngine
from app.machine import Machine
from app.retrieval import Retriever
from app.sessions import MemorySessions
from app.slack import SlackClient
from app.store import Store, make_pool

ADMIN_URL = os.environ.get("RELAY_TEST_ADMIN_URL", "postgresql://postgres:testadmin@127.0.0.1:55434/postgres")
APP_PASSWORD = "test-app-password"
MAYA = "maya.chen@example.com"


def app_url(admin_url: str) -> str:
    p = urlsplit(admin_url)
    return urlunsplit((p.scheme, f"relay_app:{APP_PASSWORD}@{p.netloc.rsplit('@', 1)[-1]}", p.path, p.query, p.fragment))


@pytest.fixture(scope="session")
def database_url() -> str:
    bootstrap(ADMIN_URL, APP_PASSWORD, Settings().embed_model)
    return app_url(ADMIN_URL)


@pytest.fixture(scope="session")
def pool(database_url):
    pool = make_pool(database_url)
    yield pool
    pool.close()


@pytest.fixture(scope="session")
def retriever(pool):
    return Retriever(pool, Embedder(Settings().embed_model))


@pytest.fixture
def make_machine(pool, retriever):
    def build(engines: dict | None = None, slack: SlackClient | None = None, **settings) -> Machine:
        return Machine(Settings(database_url="", **settings), Store(pool), MemorySessions(), retriever,
                       engines or {"rules": RuleEngine()}, slack or SlackClient(""))

    return build


@pytest.fixture
def machine(make_machine):
    return make_machine()

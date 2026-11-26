"""Conversation state, the per-conversation lock and rate counters. Redis in deployment; in memory for tests and evals."""
from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
import uuid

TTL = 24 * 3600


def new_state(customer: str, engine: str) -> dict:
    return {"customer": customer, "engine": engine, "state": "open", "failed": 0, "turns": 0, "ticket_id": None,
            "previous_question": ""}


class Busy(RuntimeError):
    """Another message of this conversation is still being processed."""


def _visitor_key(visitor: str, day: str) -> str:
    return f"relay:live:{day}:{hashlib.sha256(visitor.encode()).hexdigest()[:24]}"  # no raw addresses stored


class MemorySessions:
    def __init__(self):
        self._data: dict[str, str] = {}
        self._locks: set[str] = set()
        self._counts: dict[str, int] = {}
        self._mutex = threading.Lock()

    def load(self, sid: str) -> dict | None:
        raw = self._data.get(sid)
        return json.loads(raw) if raw else None

    def save(self, sid: str, state: dict) -> None:
        self._data[sid] = json.dumps(state)

    @contextlib.contextmanager
    def lock(self, sid: str):
        with self._mutex:
            if sid in self._locks:
                raise Busy(sid)
            self._locks.add(sid)
        try:
            yield
        finally:
            with self._mutex:
                self._locks.discard(sid)

    def hit(self, key: str) -> int:
        bucket = f"{key}:{int(time.time() // 60)}"
        with self._mutex:
            self._counts[bucket] = self._counts.get(bucket, 0) + 1
            return self._counts[bucket]

    def day_hit(self, visitor: str, day: str) -> int:
        key = _visitor_key(visitor, day)
        with self._mutex:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

    def day_count(self, visitor: str, day: str) -> int:
        return self._counts.get(_visitor_key(visitor, day), 0)

    def ping(self) -> None:
        return None


class RedisSessions:
    _RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"

    def __init__(self, url: str):
        import redis

        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=3)

    def load(self, sid: str) -> dict | None:
        raw = self.r.get(f"relay:s:{sid}")
        return json.loads(raw) if raw else None

    def save(self, sid: str, state: dict) -> None:
        self.r.set(f"relay:s:{sid}", json.dumps(state), ex=TTL)

    @contextlib.contextmanager
    def lock(self, sid: str):
        key, token = f"relay:lock:{sid}", uuid.uuid4().hex
        if not self.r.set(key, token, nx=True, ex=60):
            raise Busy(sid)
        try:
            yield
        finally:
            self.r.eval(self._RELEASE, 1, key, token)

    def hit(self, key: str) -> int:
        bucket = f"relay:rate:{key}:{int(time.time() // 60)}"
        pipe = self.r.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, 120)
        return pipe.execute()[0]

    def day_hit(self, visitor: str, day: str) -> int:
        key = _visitor_key(visitor, day)
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 2 * TTL)
        return pipe.execute()[0]

    def day_count(self, visitor: str, day: str) -> int:
        return int(self.r.get(_visitor_key(visitor, day)) or 0)

    def ping(self) -> None:
        self.r.ping()

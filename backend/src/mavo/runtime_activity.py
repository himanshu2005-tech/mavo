"""Bounded, metadata-only signals for actual worker and model-request lifetimes.

This is a live UI feed, not a job queue or an execution audit. CrewRun remains durable truth.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


@dataclass
class _Scope:
    feed: ActivityFeed
    stream: str
    crew_id: str
    run_id: str | None = None
    route_id: str | None = None
    provider: str | None = None
    model: str | None = None
    credential_id: str | None = None  # comparison only; never serialized


_current: ContextVar[_Scope | None] = ContextVar("mavo_activity_scope", default=None)


class ActivityFeed:
    def __init__(self):
        self._lock = threading.RLock()
        self._events: deque[tuple[float, dict]] = deque(maxlen=512)
        self._runs: dict[str, dict] = {}
        self._calls: dict[str, dict] = {}
        self._cursor = 0
        self._stream = str(uuid4())

    def clear(self):
        with self._lock:
            self._stream = str(uuid4())
            self._events.clear()
            self._runs.clear()
            self._calls.clear()
            self._cursor = 0

    def _valid(self, ctx):
        return ctx and ctx.feed is self and ctx.stream == self._stream and ctx.run_id

    def _record(self, kind, ctx, **fields):
        if not self._valid(ctx):
            return None
        self._cursor += 1
        event = {
            "seq": self._cursor,
            "kind": kind,
            "at": datetime.now(UTC).isoformat(),
            "crew_id": ctx.crew_id,
            "run_id": ctx.run_id,
            "route_id": ctx.route_id,
            **fields,
        }
        self._events.append((time.monotonic(), event))
        return event

    @contextmanager
    def scope(self, crew_id):
        ctx = _Scope(self, self._stream, crew_id)
        token = _current.set(ctx)
        try:
            yield
        finally:
            with self._lock:
                # An unexpected escape is not a successful completion.
                if self._valid(ctx) and ctx.run_id in self._runs:
                    self.finish_run(ctx.run_id, "interrupted")
            _current.reset(token)

    def start_run(self, run_id, route):
        ctx = _current.get()
        if not ctx or ctx.feed is not self or ctx.stream != self._stream:
            return
        ctx.run_id, ctx.route_id = run_id, route.id
        ctx.provider, ctx.model, ctx.credential_id = (
            route.provider,
            route.model,
            route.credential_id,
        )
        with self._lock:
            event = self._record("run_started", ctx)
            if event:
                self._runs[run_id] = event

    def finish_run(self, run_id, status):
        ctx = _current.get()
        with self._lock:
            if self._valid(ctx) and ctx.run_id == run_id and run_id in self._runs:
                self._record("run_finished", ctx, status=status)
                self._runs.pop(run_id, None)

    @contextmanager
    def model_call(self, provider, model, credential_id):
        ctx = _current.get()
        call_id = str(uuid4())
        route_id = (
            ctx.route_id
            if (
                ctx
                and (ctx.provider, ctx.model, ctx.credential_id) == (provider, model, credential_id)
            )
            else None
        )
        with self._lock:
            event = self._record(
                "model_started",
                ctx,
                call_id=call_id,
                route_id=route_id,
                provider=provider,
                model=model,
            )
            if event:
                self._calls[call_id] = event
        status = "responded"
        try:
            yield
        except BaseException as exc:
            status = "cancelled" if type(exc).__name__ == "CancelledError" else "failed"
            raise
        finally:
            with self._lock:
                if self._calls.pop(call_id, None):
                    self._record(
                        "model_finished",
                        ctx,
                        call_id=call_id,
                        route_id=route_id,
                        provider=provider,
                        model=model,
                        status=status,
                    )

    def snapshot(self, after=None):
        with self._lock:
            while self._events and self._events[0][0] < time.monotonic() - 120:
                self._events.popleft()
            oldest = self._events[0][1]["seq"] if self._events else self._cursor + 1
            reset = after is not None and (after > self._cursor or after < oldest - 1)
            events = [] if after is None else [dict(e) for _, e in self._events if e["seq"] > after]
            return {
                "stream_id": self._stream,
                "cursor": self._cursor,
                "reset": reset,
                "events": events,
                "active_runs": [dict(e) for e in self._runs.values()],
                "active_calls": [dict(e) for e in self._calls.values()],
            }

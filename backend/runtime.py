"""In-process runtime state: per-user chat memory, rate limiting and daily
Gemini quota tracking. All structures are thread-safe because FastAPI runs
sync handlers (and the agent) inside a threadpool."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from datetime import date

class ChatMemory:
    """Bounded short-term conversation memory keyed by WhatsApp number.

    Keeps the last `max_turns` human/ai exchanges so the agent can resolve
    follow-ups like "from majestic?" after asking a clarifying question.
    """

    def __init__(self, max_turns: int = 4, max_users: int = 500) -> None:
        self._max_entries: int = max_turns * 2
        self._max_users: int = max_users
        self._store: OrderedDict[str, deque[tuple[str, str]]] = OrderedDict()
        self._lock = threading.Lock()

    def history(self, user: str) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._store.get(user, ()))

    def append(self, user: str, role: str, content: str) -> None:
        trimmed: str = content.strip()
        if not trimmed:
            return
        with self._lock:
            bucket: deque[tuple[str, str]] | None = self._store.get(user)
            if bucket is None:
                if len(self._store) >= self._max_users:
                    self._store.popitem(last=False)
                bucket = deque(maxlen=self._max_entries)
                self._store[user] = bucket
            else:
                self._store.move_to_end(user)
            bucket.append((role, trimmed))

    def reset(self, user: str) -> None:
        with self._lock:
            self._store.pop(user, None)


class RateLimiter:
    """Sliding-window limiter: at most `max_events` events per `window_seconds`
    for each key. Returns True when an action is allowed."""

    def __init__(self, max_events: int = 10, window_seconds: float = 60.0) -> None:
        self._max_events: int = max_events
        self._window: float = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now: float = time.monotonic()
        cutoff: float = now - self._window
        with self._lock:
            bucket: deque[float] = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_events:
                return False
            bucket.append(now)
            return True


class UsageTracker:
    """Daily counters for Gemini requests per model plus direct tool answers.
    Counters roll over automatically when the local date changes."""

    def __init__(self, models: list[str]) -> None:
        self._models: list[str] = list(models)
        self._day: str = date.today().isoformat()
        self._model_hits: dict[str, int] = {m: 0 for m in models}
        self._model_fails: dict[str, int] = {m: 0 for m in models}
        self._direct_answers: int = 0
        self._agent_questions: int = 0
        self._lock = threading.Lock()

    def _rollover_if_needed(self) -> None:
        today: str = date.today().isoformat()
        if today != self._day:
            self._day = today
            self._model_hits = {m: 0 for m in self._models}
            self._model_fails = {m: 0 for m in self._models}
            self._direct_answers = 0
            self._agent_questions = 0

    def record_direct(self) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._direct_answers += 1

    def record_agent_hit(self, model: str) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._agent_questions += 1
            self._model_hits[model] = self._model_hits.get(model, 0) + 1

    def record_agent_fail(self, model: str) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._model_fails[model] = self._model_fails.get(model, 0) + 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._rollover_if_needed()
            known: set[str] = set(self._models)
            known.update(self._model_hits)
            known.update(self._model_fails)
            return {
                "date": self._day,
                "direct_tool_answers": self._direct_answers,
                "agent_questions": self._agent_questions,
                "per_model": {
                    m: {"successes": self._model_hits.get(m, 0), "failures": self._model_fails.get(m, 0)}
                    for m in sorted(known)
                },
                "note": "Free tier allows ~20 generateContent requests/model/day; resets midnight Pacific.",
            }

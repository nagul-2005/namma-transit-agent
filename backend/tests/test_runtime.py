"""Unit tests for runtime state: chat memory, rate limiter, usage tracker."""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import ChatMemory, RateLimiter, UsageTracker


class TestChatMemory:
    def test_append_and_history_order(self) -> None:
        mem = ChatMemory(max_turns=4)
        mem.append("u1", "human", "hello")
        mem.append("u1", "ai", "hi there")
        assert mem.history("u1") == [("human", "hello"), ("ai", "hi there")]

    def test_bounded_history(self) -> None:
        mem = ChatMemory(max_turns=2)
        for i in range(6):
            mem.append("u1", "human", f"msg {i}")
            mem.append("u1", "ai", f"reply {i}")
        history = mem.history("u1")
        assert len(history) == 4  # 2 turns * 2 entries
        assert history[-1] == ("ai", "reply 5")

    def test_blank_messages_ignored(self) -> None:
        mem = ChatMemory()
        mem.append("u1", "human", "   ")
        assert mem.history("u1") == []

    def test_users_are_isolated(self) -> None:
        mem = ChatMemory()
        mem.append("a", "human", "from a")
        mem.append("b", "human", "from b")
        assert mem.history("a")[0][1] == "from a"
        assert mem.history("b")[0][1] == "from b"

    def test_reset(self) -> None:
        mem = ChatMemory()
        mem.append("u1", "human", "x")
        mem.reset("u1")
        assert mem.history("u1") == []

    def test_max_users_eviction(self) -> None:
        mem = ChatMemory(max_users=2)
        for u in ("a", "b", "c"):
            mem.append(u, "human", f"hi {u}")
        assert mem.history("a") == []  # oldest evicted
        assert mem.history("b")[0][1] == "hi b"
        assert mem.history("c")[0][1] == "hi c"


class TestRateLimiter:
    def test_allows_within_budget(self) -> None:
        rl = RateLimiter(max_events=3, window_seconds=60.0)
        assert all(rl.allow("u1") for _ in range(3))

    def test_blocks_over_budget(self) -> None:
        rl = RateLimiter(max_events=2, window_seconds=60.0)
        assert rl.allow("u1")
        assert rl.allow("u1")
        assert not rl.allow("u1")

    def test_keys_independent(self) -> None:
        rl = RateLimiter(max_events=1, window_seconds=60.0)
        assert rl.allow("a")
        assert not rl.allow("a")
        assert rl.allow("b")

    def test_window_slides(self) -> None:
        import time

        rl = RateLimiter(max_events=1, window_seconds=0.05)
        assert rl.allow("u1")
        assert not rl.allow("u1")
        time.sleep(0.06)
        assert rl.allow("u1")


class TestUsageTracker:
    def test_counts_roll_up(self) -> None:
        ut = UsageTracker(models=["m1", "m2"])
        ut.record_direct()
        ut.record_direct()
        ut.record_agent_hit("m1")
        ut.record_agent_fail("m2")
        snap = ut.snapshot()
        assert snap["direct_tool_answers"] == 2
        assert snap["agent_questions"] == 1
        per_model = snap["per_model"]
        assert per_model["m1"]["successes"] == 1  # type: ignore[index]
        assert per_model["m2"]["failures"] == 1  # type: ignore[index]

    def test_unknown_model_counted(self) -> None:
        ut = UsageTracker(models=[])
        ut.record_agent_hit("brand-new-model")
        assert ut.snapshot()["per_model"]["brand-new-model"]["successes"] == 1  # type: ignore[index]

    def test_snapshot_has_date(self) -> None:
        from datetime import date

        ut = UsageTracker(models=[])
        assert ut.snapshot()["date"] == date.today().isoformat()

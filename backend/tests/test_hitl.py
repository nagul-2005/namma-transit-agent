"""Tests for LangGraph Human-in-the-Loop (HITL) multi-turn state cycle with MemorySaver."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, TypedDict

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt


class HITLState(TypedDict):
    messages: Annotated[list, add_messages]
    user_approved: bool


def agent_node(state: HITLState):
    current_approved = state.get("user_approved", False)
    if not current_approved:
        approved_response = interrupt("Review Fare?")
        return {
            "messages": [AIMessage(content="Fare approved by user.")],
            "user_approved": bool(approved_response),
        }
    return {
        "messages": [AIMessage(content="Processing request directly.")]
    }


def processor_node_option_a(state: HITLState):
    """Option A: Reset transient user_approved flag in the finalizer node."""
    return {
        "messages": [AIMessage(content="Ticket booked successfully!")],
        "user_approved": False,
    }


def build_test_graph():
    builder = StateGraph(HITLState)
    builder.add_node("agent", agent_node)
    builder.add_node("processor", processor_node_option_a)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", "processor")
    builder.add_edge("processor", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


class TestHITLMultiTurnCycle:
    def test_multi_turn_hitl_cycle_option_a(self):
        graph = build_test_graph()
        config = {"configurable": {"thread_id": "test_thread_option_a"}}

        # --- TURN 1: Initial user message ---
        turn_1_events = list(
            graph.stream(
                {
                    "messages": [HumanMessage(content="Book metro ticket from Majestic to Indiranagar")],
                    "user_approved": False,
                },
                config=config,
                stream_mode="updates",
            )
        )
        assert len(turn_1_events) > 0

        # Verify state is paused at interrupt
        state_t1_paused = graph.get_state(config)
        assert state_t1_paused.next == ("agent",)
        assert len(state_t1_paused.tasks) > 0
        assert state_t1_paused.tasks[0].interrupts[0].value == "Review Fare?"

        # --- TURN 1 RESUMPTION ---
        resume_events = list(
            graph.stream(Command(resume=True), config=config, stream_mode="updates")
        )
        assert len(resume_events) > 0

        # Verify graph finished and Option A reset user_approved to False
        state_t1_done = graph.get_state(config)
        assert state_t1_done.next == ()
        assert state_t1_done.values.get("user_approved") is False

        # --- TURN 2: Secondary text message sent to the SAME thread_id ---
        turn_2_events = list(
            graph.stream(
                {
                    "messages": [HumanMessage(content="Now book a ticket from Indiranagar to Whitefield")]
                },
                config=config,
                stream_mode="updates",
            )
        )
        assert len(turn_2_events) > 0

        # Verify Turn 2 is NOT skipped or frozen, but cleanly paused at interrupt again
        state_t2_paused = graph.get_state(config)
        assert state_t2_paused.next == ("agent",)
        assert len(state_t2_paused.tasks) > 0
        assert state_t2_paused.tasks[0].interrupts[0].value == "Review Fare?"

        # --- TURN 2 RESUMPTION ---
        resume_t2_events = list(
            graph.stream(Command(resume=True), config=config, stream_mode="updates")
        )
        assert len(resume_t2_events) > 0

        state_t2_done = graph.get_state(config)
        assert state_t2_done.next == ()
        assert state_t2_done.values.get("user_approved") is False

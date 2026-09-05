"""Unit tests for the zero-cost intent pre-router in main.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")  # agent import guard

from main import _pre_route  # noqa: E402


class TestDirectRoutes:
    def test_help_menu(self) -> None:
        assert "Namma Transit Agent" in (_pre_route("hi") or "")
        assert "Namma Transit Agent" in (_pre_route("HELP") or "")

    def test_metro_from_to(self) -> None:
        out = _pre_route("metro fare from majestic to whitefield")
        assert out is not None and "₹90" in out

    def test_reversed_to_from_with_metro_keyword(self) -> None:
        out = _pre_route("metro from koramangala to majestic")
        assert out is not None and "Majestic" in out

    def test_generic_journey_without_keyword_triggers_mode_hitl(self) -> None:
        out = _pre_route("i want to go from majestic to indiranagar")
        assert out is not None and "Transport Mode Selection" in out

    def test_bus_tracking_falls_through_to_gemini(self) -> None:
        # Bus tracking is routed to Gemini LLM with tool calling
        assert _pre_route("is bus 335e on time") is None
        assert _pre_route("check bmtc route 500C status") is None

        # Verify the underlying tool executes accurately
        from tools import check_bmtc_bus_status
        tool_out = check_bmtc_bus_status.invoke({"route_no": "335E"})
        assert "335E" in tool_out
        assert "DELAYED" in tool_out

    def test_auto_with_distance(self) -> None:
        out = _pre_route("auto fare for 7 km")
        assert out is not None and "7.0 km" in out

    def test_namma_yatri_compact_km(self) -> None:
        out = _pre_route("namma yatri price for 4km")
        assert out is not None and "*₹60*" in out


class TestHitlClarification:
    def test_destination_only_query_triggers_hitl(self) -> None:
        out = _pre_route("i want to go to indiranagar")
        assert out is not None
        assert "Human-In-The-Loop Clarification" in out
        assert "Indiranagar" in out
        assert "1. From Kempegowda (Majestic)" in out
        # Ensure zero emojis in option lines
        options_block = out.split("Select your starting location:")[1].split("Reply with your choice")[0]
        assert "🚇" not in options_block
        assert "🚌" not in options_block
        assert "🛺" not in options_block

    def test_how_to_reach_destination_triggers_hitl(self) -> None:
        out = _pre_route("how to reach electronic city")
        assert out is not None
        assert "Electronic City" in out
        assert "Select your starting location:" in out

    def test_mode_selection_hitl_triggered(self) -> None:
        out = _pre_route("I want to go from Halasuru to Church Street")
        assert out is not None
        assert "Human-In-The-Loop Transport Mode Selection" in out
        assert "Halasuru" in out
        assert "Church Street" in out
        assert "1. Metro (Namma Metro Purple/Green/Yellow)" in out
        assert "2. Buses (BMTC Direct & Feeder)" in out
        assert "3. Taxi / Auto (Namma Yatri / Cab)" in out
        assert "4. Mix (Metro + Bus / Auto)" in out

    def test_typo_bangalore_destination_triggers_hitl(self) -> None:
        out = _pre_route("I want to go to chruch street")
        assert out is not None
        assert "Human-In-The-Loop Clarification" in out
        assert "Church Street" in out
        assert "Select your starting location:" in out

    def test_custom_origin_answer_following_clarification(self) -> None:
        history = [
            ("human", "I want to go to chruch street"),
            ("ai", "*Namma Transit Agent — Human-In-The-Loop Clarification*\n\nYou want to reach *Church Street*... where will you be starting your journey?")
        ]
        out = _pre_route("halasuru", chat_history=history)
        assert out is not None
        assert "Human-In-The-Loop Transport Mode Selection" in out
        assert "Halasuru" in out
        assert "Church Street" in out
        assert "1. Metro" in out

    def test_mode_options_buses_selection(self) -> None:
        history = [
            ("ai", "*Namma Transit Agent — Human-In-The-Loop Transport Mode Selection*\n\nYou want to travel from *Halasuru* to *Church Street*. Which transport mode do you prefer?")
        ]
        out = _pre_route("2. Buses (BMTC Direct & Feeder)", chat_history=history)
        assert out is not None
        assert "BMTC Bus Routes" in out
        assert "Halasuru ➔ Church Street" in out
        assert "OpenStreetMap Road Distance" in out
        assert "Nearest Boarding Stop" in out
        assert "Vajra AC Volvo" in out

    def test_osm_geocoding_and_fares(self) -> None:
        from tools import calculate_bmtc_fare, geocode_osm_location
        lat, lon, stop = geocode_osm_location("Majestic")
        assert lat > 12.0 and lon > 77.0
        assert "Majestic" in stop

        ord_fare, vajra_fare = calculate_bmtc_fare(12.0)
        assert "₹25" in ord_fare
        assert "₹55" in vajra_fare

    def test_mode_options_auto_selection(self) -> None:
        history = [
            ("ai", "*Namma Transit Agent — Human-In-The-Loop Transport Mode Selection*\n\nYou want to travel from *Halasuru* to *Church Street*. Which transport mode do you prefer?")
        ]
        out = _pre_route("3. Taxi / Auto (Namma Yatri / Cab)", chat_history=history)
        assert out is not None
        assert "Auto Fare Estimate" in out
        assert "Base fare (first 2 km): ₹30" in out

    def test_mode_options_mix_selection(self) -> None:
        history = [
            ("ai", "*Namma Transit Agent — Human-In-The-Loop Transport Mode Selection*\n\nYou want to travel from *Halasuru* to *Church Street*. Which transport mode do you prefer?")
        ]
        out = _pre_route("4. Mix (Metro + Bus / Auto)", chat_history=history)
        assert out is not None
        assert "Multimodal Mix Recommendation" in out
        assert "Primary Leg (Namma Metro)" in out

    def test_cheapest_way_falls_through_to_gemini(self) -> None:
        # Comparison queries fall through to Gemini LLM for smart evaluation
        assert _pre_route("Suggest a cheapest way from halasuru to church street") is None

        # Verify comparison tool executes accurately
        from tools import get_all_transport_comparison
        comp_out = get_all_transport_comparison.invoke({"origin": "Halasuru", "destination": "Church Street"})
        assert "All Transport Options" in comp_out
        assert "Namma Metro" in comp_out
        assert "BMTC Bus" in comp_out
        assert "Auto / Cab (Namma Yatri)" in comp_out


class TestBengaluruGeofence:
    def test_outside_bangalore_origin_dest_falls_through_to_gemini(self) -> None:
        # Outstation queries fall through to Gemini LLM for conversational guidance
        assert _pre_route("Suggest a way from halasuru to Pataya") is None
        assert _pre_route("How to reach Chennai from Majestic") is None
        assert _pre_route("I want to go to Delhi") is None

        from main import _detect_outside_bangalore
        assert _detect_outside_bangalore("Suggest a way from halasuru to Pataya") is True
        assert _detect_outside_bangalore("How to reach Chennai from Majestic") is True
        assert _detect_outside_bangalore("I want to go to Delhi") is True

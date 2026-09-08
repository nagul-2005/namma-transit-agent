"""Unit tests for the simulated transit tools."""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tools import (
    check_bmtc_bus_status,
    estimate_namma_yatri_fare,
    get_metro_schedule,
)


class TestMetroSchedule:
    def test_adjacent_stations_are_cheap(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Indiranagar", "destination": "Halasuru"})
        assert "1.3 km" in out
        assert "*₹10*" in out

    def test_halasuru_to_whitefield_real_fare(self) -> None:
        # Real distance 20.1 km -> slab 20-25 km -> Rs 80 token (Feb-2025 slabs).
        out = get_metro_schedule.invoke({"origin": "Halasuru", "destination": "Whitefield"})
        assert "20.1 km" in out
        assert "*₹80*" in out
        assert "~₹76 peak" in out
        assert "~₹72 non-peak" in out

    def test_long_haul_fare(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Majestic", "destination": "Whitefield"})
        assert "26.2 km" in out
        assert "*₹90*" in out

    def test_full_line_end_to_end(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Whitefield", "destination": "Challaghatta"})
        assert "43.5 km" in out
        assert "*₹90*" in out

    def test_green_line_corridor(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Nagasandra", "destination": "Silk Institute"})
        assert "Madavara ↔ Silk Institute" in out
        assert "30.4 km" in out

    def test_cross_line_via_majestic_is_short(self) -> None:
        # Kempegowda (Majestic) → Chickpete is one Green-line stop.
        out = get_metro_schedule.invoke({"origin": "Kempegowda", "destination": "Chickpet"})
        assert "interchange" in out.lower() or "Green" in out
        assert "1.0 km" in out
        assert "*₹10*" in out

    def test_alias_mg_road_resolves(self) -> None:
        out = get_metro_schedule.invoke({"origin": "MG Road", "destination": "Trinity"})
        assert "Mahatma Gandhi Road → Trinity" in out
        assert "1.1 km" in out

    def test_partial_station_name_matches(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Mahatma Gandhi", "destination": "Indiranagar"})
        assert "Mahatma Gandhi Road → Indiranagar" in out

    def test_unknown_station_returns_error(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Kammanahalli", "destination": "Thyagaraja Nagar"})
        assert "Sorry" in out or "couldn't find" in out
        assert "Kammanahalli" in out

    def test_single_unknown_station_returns_error(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Madiwala", "destination": "Majestic"})
        assert "Sorry" in out or "couldn't find" in out
        assert "Madiwala" in out

    def test_yelachenahalli_maps_to_green_station(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Yelachenahalli", "destination": "Jayanagar"})
        assert "Yelachenahalli → Jayanagar" in out

    def test_majestic_delay_advisory(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Majestic", "destination": "Indiranagar"})
        assert "15-minute signaling delay" in out

    def test_normal_operations_line(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Jayanagar", "destination": "Lalbagh"})
        assert "Operations normal" in out

    def test_yellow_line_corridor(self) -> None:
        out = get_metro_schedule.invoke({"origin": "RV Road", "destination": "Electronic City"})
        assert "Electronic City" in out
        assert "13.5 km" in out

    def test_yellow_green_interchange(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Jayanagar", "destination": "Silk Board"})
        assert "interchange at RV Road" in out
        assert "Central Silk Board" in out

    def test_station_with_punctuation_and_suffixes(self) -> None:
        out = get_metro_schedule.invoke({"origin": "Majestic.", "destination": "Indiranagar."})
        assert "Kempegowda (Majestic) → Indiranagar" in out
        assert "Token fare" in out

        out_suffix = get_metro_schedule.invoke({"origin": "Majestic metro", "destination": "Whitefield station."})
        assert "Kempegowda (Majestic) → Whitefield (Kadugodi)" in out_suffix


class TestBmtcStatus:
    @pytest.mark.parametrize("route", ["335E", "335e"])
    def test_silk_board_bottleneck(self, route: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BMTC_TELEMETRY_API_URL", "")
        out = check_bmtc_bus_status.invoke({"route_no": route})
        assert "335E" in out
        assert "Silk Board" in out
        assert "+18 minutes" in out

    def test_marathahalli_bottleneck(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BMTC_TELEMETRY_API_URL", "")
        out = check_bmtc_bus_status.invoke({"route_no": "500C"})
        assert "Marathahalli" in out
        assert "+14 minutes" in out

    def test_generic_route_on_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BMTC_TELEMETRY_API_URL", "")
        out = check_bmtc_bus_status.invoke({"route_no": "201R"})
        assert "ON TIME" in out

    def test_live_bmtc_telemetry_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tools
        monkeypatch.setenv("BMTC_TELEMETRY_API_URL", "https://mock.bmtc.test/api/{route}")

        class MockResp:
            status_code = 200
            def json(self) -> dict:
                return {
                    "location": "Hebbal Flyover",
                    "status": "ON TIME",
                    "delay_minutes": 0,
                    "next_stop": "Hebbal Station",
                }

        monkeypatch.setattr(tools.httpx, "get", lambda url, timeout: MockResp())
        out = check_bmtc_bus_status.invoke({"route_no": "500D"})
        assert "Live Telemetry (API)" in out
        assert "Hebbal Flyover" in out


class TestAutoFare:
    def test_short_trip_base_fare(self) -> None:
        out = estimate_namma_yatri_fare.invoke({"distance_km": 1.5})
        assert "₹30" in out

    def test_exact_boundary_two_km(self) -> None:
        out = estimate_namma_yatri_fare.invoke({"distance_km": 2.0})
        assert "*₹30*" in out

    def test_standard_formula(self) -> None:
        # 5 km = 30 + (5-2)*15 = 75
        out = estimate_namma_yatri_fare.invoke({"distance_km": 5.0})
        assert "*₹75*" in out

    def test_fractional_distance(self) -> None:
        out = estimate_namma_yatri_fare.invoke({"distance_km": 7.5})
        assert "Estimated total" in out

    def test_zero_distance_clamped(self) -> None:
        out = estimate_namma_yatri_fare.invoke({"distance_km": 0.0})
        assert "0.0 km" in out
        assert "*₹30*" in out

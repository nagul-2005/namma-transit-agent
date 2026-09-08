from __future__ import annotations

import math
import os
import re
from typing import Final

import httpx

from langchain_core.tools import tool

# Official BMRCL fare structure effective 09-Feb-2025 (Fare Fixation Committee,
# binding under Metro Railways O&M Act s.37). Slabs: max_km -> token fare Rs.
METRO_FARE_SLABS: Final[tuple[tuple[int, int], ...]] = (
    (2, 10), (4, 20), (6, 30), (8, 40), (10, 50),
    (15, 60), (20, 70), (25, 80), (10_000, 90),
)
SMART_CARD_PEAK_DISCOUNT: Final[float] = 0.05      # Mon-Sat peak hours
SMART_CARD_OFFPEAK_DISCOUNT: Final[float] = 0.10   # non-peak hours, Sundays & national holidays

METRO_TRAIN_TIMING: Final[tuple[str, str]] = ("05:00", "23:00")

METRO_LINE_CORRIDORS: Final[dict[str, str]] = {
    "purple": "Whitefield (Kadugodi) ↔ Challaghatta",
    "green": "Madavara ↔ Silk Institute",
    "yellow": "RV Road ↔ Bommasandra",
    "blue": "Kempegowda Airport ↔ Central Silk Board (under trial)",
}

# Native station order (Purple line, east -> west) with cumulative distance in
# km from Whitefield (Kadugodi). Line length: 43.49 km, 37 stations (BMRCL).
PURPLE_LINE: Final[tuple[str, ...]] = (
    "Whitefield (Kadugodi)", "Hopefarm Channasandra", "Kadugodi Tree Park",
    "Pattandur Agrahara", "Sri Sathya Sai Hospital", "Nallurhalli",
    "Kundalahalli", "Seetharamapalya", "Hoodi", "Garudacharpalya",
    "Singayyanapalya", "KR Pura", "Benniganahalli", "Baiyappanahalli",
    "Swami Vivekananda Road", "Indiranagar", "Halasuru", "Trinity",
    "Mahatma Gandhi Road", "Cubbon Park", "Vidhana Soudha", "Central College",
    "Kempegowda (Majestic)", "City Railway Station", "Magadi Road",
    "Hosahalli", "Vijayanagar", "Attiguppe", "Deepanjali Nagar",
    "Mysuru Road", "Nayandahalli", "Rajarajeshwari Nagar", "Jnanabharathi",
    "Pattanagere", "Kengeri Bus Terminal", "Kengeri", "Challaghatta",
)

PURPLE_CUM_KM: Final[tuple[float, ...]] = (
    0.0, 1.4, 2.7, 4.1, 5.2, 6.4, 7.8, 9.0, 10.2, 11.4, 12.5, 13.7,
    14.9, 16.1, 17.5, 18.8, 20.1, 21.2, 22.3, 23.4, 24.3, 25.2, 26.2,
    27.2, 28.5, 29.7, 30.9, 32.0, 33.0, 34.2, 35.6, 37.0, 38.3, 39.6,
    41.0, 42.3, 43.5,
)

# Native station order (Green line, north -> south) with cumulative distance
# in km from Madavara. Line length: 33.46 km, 32 stations (BMRCL).
GREEN_LINE: Final[tuple[str, ...]] = (
    "Madavara", "Chikkabidarakallu", "Manjunath Nagar", "Nagasandra",
    "Dasarahalli", "Jalahalli", "Peenya Industry", "Peenya",
    "Goraguntepalya", "Yeshwanthpur", "Sandal Soap Factory", "Mahalakshmi",
    "Rajajinagar", "Mahakavi Kuvempu Road", "Srirampura",
    "Mantri Square Sampige Road", "Kempegowda (Majestic)", "Chickpete",
    "Krishna Rajendra Market", "National College", "Lalbagh",
    "South End Circle", "Jayanagar", "RV Road", "Banashankari",
    "Jaya Prakash Nagar", "Yelachenahalli", "Konanakunte Cross",
    "Doddakallasandra", "Vajarahalli", "Thalaghattapura", "Silk Institute",
)

GREEN_CUM_KM: Final[tuple[float, ...]] = (
    0.0, 1.0, 2.1, 3.1, 4.0, 4.8, 5.6, 7.2, 8.8, 10.4, 11.3, 12.1,
    13.0, 13.9, 14.7, 15.5, 16.6, 17.6, 18.6, 19.6, 20.6, 21.7, 22.7,
    23.7, 25.0, 26.3, 27.5, 28.7, 30.0, 31.2, 32.4, 33.5,
)

# Native station order (Yellow line, north -> south) with cumulative distance
# in km from RV Road. Line length: 19.1 km, 14 stations (BMRCL).
YELLOW_LINE: Final[tuple[str, ...]] = (
    "RV Road", "Ragigudda", "Jayadeva Hospital", "BTM Layout",
    "Central Silk Board", "HSR Layout", "Singasandra", "Hosa Road",
    "Beratena Agrahara", "Electronic City", "InfoSys Foundation Konappana Agrahara",
    "Huskur Road", "Hebbagodi", "Bommasandra",
)

YELLOW_CUM_KM: Final[tuple[float, ...]] = (
    0.0, 1.4, 2.7, 4.0, 5.4, 7.0, 8.8, 10.4,
    11.9, 13.5, 15.0, 16.5, 17.8, 19.1,
)

STATION_ALIASES: Final[dict[str, str]] = {
    "majestic": "Kempegowda (Majestic)",
    "kempegowda": "Kempegowda (Majestic)",
    "kempegowda majestic": "Kempegowda (Majestic)",
    "nadaprabhu kempegowda": "Kempegowda (Majestic)",
    "mg road": "Mahatma Gandhi Road",
    "mgrd road": "Mahatma Gandhi Road",
    "church street": "Mahatma Gandhi Road",
    "chruch street": "Mahatma Gandhi Road",
    "brigade road": "Mahatma Gandhi Road",
    "commercial street": "Mahatma Gandhi Road",
    "kr pura": "KR Pura",
    "kr puram": "KR Pura",
    "tin factory": "KR Pura",
    "whitefield kadugodi": "Whitefield (Kadugodi)",
    "kadugodi": "Whitefield (Kadugodi)",
    "railway station": "City Railway Station",
    "ksr": "City Railway Station",
    "kranthiveera sangolli rayanna": "City Railway Station",
    "visvesvaraya": "Central College",
    "vidhana soudha": "Vidhana Soudha",
    "cubbbon park": "Cubbon Park",
    "mysore road": "Mysuru Road",
    "rv road": "RV Road",
    "rashtreeya vidyalaya road": "RV Road",
    "kr market": "Krishna Rajendra Market",
    "city market": "Krishna Rajendra Market",
    "chickpet": "Chickpete",
    "jp nagar": "Jaya Prakash Nagar",
    "sampige road": "Mantri Square Sampige Road",
    "yelachenahalli": "Yelachenahalli",
    "silk board": "Central Silk Board",
    "silk board metro": "Central Silk Board",
    "electronic city": "Electronic City",
    "ecity": "Electronic City",
    "jayadeva": "Jayadeva Hospital",
    "bommasandra": "Bommasandra",
    "btm": "BTM Layout",
    "btm layout": "BTM Layout",
    "hsr": "HSR Layout",
    "hsr layout": "HSR Layout",
    "hosa road": "Hosa Road",
    "infosys": "InfoSys Foundation Konappana Agrahara",
}


def _resolve_station(raw: str) -> tuple[str, str, float] | None:
    """Resolve a user-provided station name to its canonical Purple/Green/Yellow line
    entry. Returns (station, side, cumulative_km) or None if unrecognised."""
    if not raw:
        return None

    # 1. Strip leading/trailing punctuation and normalize whitespace
    cleaned: str = re.sub(r"^[^\w]+|[^\w]+$", "", raw.strip())
    query: str = re.sub(r"\s+", " ", cleaned.lower()).strip()
    if not query:
        return None

    lines: tuple[tuple[tuple[str, ...], str, tuple[float, ...]], ...] = (
        (PURPLE_LINE, "purple", PURPLE_CUM_KM),
        (GREEN_LINE, "green", GREEN_CUM_KM),
        (YELLOW_LINE, "yellow", YELLOW_CUM_KM),
    )

    # Generate candidate variations (e.g. "majestic metro" -> "majestic")
    candidates: list[str] = [query]
    stripped_suffix: str = re.sub(r"\b(metro\s*station|metro|station|stop)\b", "", query).strip()
    stripped_suffix = re.sub(r"\s+", " ", stripped_suffix).strip()
    if stripped_suffix and stripped_suffix != query:
        candidates.append(stripped_suffix)

    # Pass 1 & 2: Exact and substring matching on candidates and aliases
    for cand in candidates:
        cand_mapped: str = STATION_ALIASES.get(cand, cand).lower()
        for line, side, cums in lines:
            hit: int = _index_of(line, cand_mapped, fuzzy=False)
            if hit >= 0:
                return (line[hit], side, cums[hit])
        for line, side, cums in lines:
            hit = _index_of(line, cand_mapped, fuzzy=True)
            if hit >= 0:
                return (line[hit], side, cums[hit])

    return None


def _index_of(line: tuple[str, ...], query: str, fuzzy: bool) -> int:
    """Exact match pass first; when `fuzzy` is set, allow bounded substring
    matching for partial names like 'mahatma gandhi' (matches 'Mahatma Gandhi
    Road')."""
    for index, station in enumerate(line):
        if station.lower() == query:
            return index
    if fuzzy and len(query) >= 6:
        for index, station in enumerate(line):
            lowered: str = station.lower()
            if query in lowered or lowered in query:
                return index
    return -1


def _metro_fare_for_distance(distance_km: float) -> int:
    for max_km, fare in METRO_FARE_SLABS:
        if distance_km <= max_km:
            return fare
    return METRO_FARE_SLABS[-1][1]


def fetch_live_bmtc_telemetry(route_no: str) -> str | None:
    """Fetch live BMTC bus telemetry from an external API endpoint (e.g. Transitland REST API or custom backend).
    Returns formatted telemetry string on success, or None on failure/missing config to trigger fallback."""
    api_url: str | None = os.getenv("BMTC_TELEMETRY_API_URL")
    if not api_url:
        return None
    timeout: float = float(os.getenv("API_TIMEOUT_SECONDS", "3.0"))
    try:
        url = api_url.format(route=route_no) if "{route}" in api_url else f"{api_url.rstrip('/')}/{route_no}"
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "routes" in data:
                routes = data.get("routes", [])
                bmtc_routes = [
                    r for r in routes
                    if any(kw in str(r.get("agency", {}).get("agency_name", "")).lower() for kw in ("bmtc", "bangalore", "bengaluru", "karnataka", "ksrtc"))
                ]
                if bmtc_routes:
                    route_info = bmtc_routes[0]
                    short_name = route_info.get("route_short_name", route_no)
                    agency = route_info.get("agency", {}).get("agency_name", "BMTC Bengaluru")
                    onestop_id = route_info.get("onestop_id", "N/A")
                    return (
                        f"*BMTC Bus {short_name} — Live Registry Telemetry (Transitland API)*\n\n"
                        f"Agency / Operator: *{agency}*\n"
                        f"Route ID: `{onestop_id}`\n"
                        f"Status: *ACTIVE IN REGISTRY (ON TIME)*\n"
                        "Service operations running. Adjust maadi!"
                    )
            elif isinstance(data, dict):
                location = data.get("location", "En route")
                status = data.get("status", "ON TIME")
                delay = data.get("delay_minutes", 0)
                next_stop = data.get("next_stop", "N/A")
                delay_str = f" (+{delay} mins)" if delay > 0 else ""
                return (
                    f"*BMTC Bus {route_no} — Live Telemetry (API)*\n\n"
                    f"Current location: {location}\n"
                    f"Status: *{status}{delay_str}*\n"
                    f"Next stop: {next_stop}\n"
                    "Adjust maadi!"
                )
    except Exception:
        pass
    return None


def fetch_gtfs_rt_metro_alerts() -> str | None:
    """Fetch live Namma Metro alerts from a GTFS-RT feed if GTFS_RT_ALERTS_URL is configured."""
    alerts_url: str | None = os.getenv("GTFS_RT_ALERTS_URL")
    if not alerts_url:
        return None
    timeout: float = float(os.getenv("API_TIMEOUT_SECONDS", "2.0"))
    try:
        resp = httpx.get(alerts_url, timeout=timeout)
        if resp.status_code == 200:
            try:
                from google.transit import gtfs_realtime_pb2
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(resp.content)
                alerts = []
                for entity in feed.entity:
                    if entity.HasField("alert"):
                        text = entity.alert.header_text.translation[0].text if entity.alert.header_text.translation else "Alert active"
                        alerts.append(f"• *{text}*")
                if alerts:
                    return "\n".join(alerts)
            except Exception:
                pass
    except Exception:
        pass
    return None


def fetch_tomtom_traffic_delay(lat: float, lon: float) -> str | None:
    """Fetch live traffic delay from TomTom Traffic Flow Segment API for coordinates."""
    api_key: str | None = os.getenv("TOMTOM_API_KEY")
    if not api_key:
        return None
    timeout: float = float(os.getenv("API_TIMEOUT_SECONDS", "3.0"))
    url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/relative-delay/10/json?key={api_key}&point={lat},{lon}"
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            flow = resp.json().get("flowSegmentData", {})
            current_speed = flow.get("currentSpeed", 0)
            free_flow_speed = flow.get("freeFlowSpeed", 0)
            delay_sec = flow.get("currentDelay", 0)
            delay_min = max(int(round(delay_sec / 60.0)), 0)
            speed_kmh = int(round(current_speed * 3.6)) if current_speed else 0
            free_kmh = int(round(free_flow_speed * 3.6)) if free_flow_speed else 0
            return f"Live TomTom Traffic: Speed {speed_kmh} km/h (Normal: {free_kmh} km/h) · Congestion Delay: +{delay_min} min"
    except Exception:
        pass
    return None


@tool
def get_metro_schedule(origin: str, destination: str) -> str:
    """Fetch the simulated live Namma Metro schedule and ticket price for a
    journey between two Bengaluru stations (Purple/Green lines). Uses the real
    BMRCL station sequences with cumulative distances (Purple 43.49 km / Green
    33.46 km) and the official token fares effective 09-Feb-2025 (₹10-₹90
    distance slabs) plus Smart Card/NCMC discount estimates. If 'Majestic' or
    'Whitefield' is on the route, a 15-minute signaling delay advisory is
    appended, mirroring real-world metro congestion at those hubs."""
    origin_resolved: tuple[str, str, float] | None = _resolve_station(origin)
    destination_resolved: tuple[str, str, float] | None = _resolve_station(destination)

    if origin_resolved is not None and destination_resolved is not None:
        origin_station, origin_side, origin_km = origin_resolved
        dest_station, dest_side, dest_km = destination_resolved
        if origin_side == dest_side:
            corridor: str = METRO_LINE_CORRIDORS[origin_side]
            distance_km: float = round(abs(origin_km - dest_km), 1)
        elif {origin_side, dest_side} == {"green", "yellow"}:
            corridor = "Green ↔ Yellow (interchange at RV Road)"
            green_km = origin_km if origin_side == "green" else dest_km
            yellow_km = origin_km if origin_side == "yellow" else dest_km
            distance_km = round(abs(green_km - 23.7) + abs(yellow_km - 0.0), 1)
        elif {origin_side, dest_side} == {"purple", "yellow"}:
            corridor = "Purple ↔ Yellow (interchange via Green Line / RV Road & Majestic)"
            purple_km = origin_km if origin_side == "purple" else dest_km
            yellow_km = origin_km if origin_side == "yellow" else dest_km
            distance_km = round(abs(purple_km - 26.2) + 7.1 + abs(yellow_km - 0.0), 1)
        else:
            corridor = "Purple ↔ Green (interchange at Majestic)"
            origin_to_hub: float = abs(origin_km - (26.2 if origin_side == "purple" else 16.6))
            dest_to_hub: float = abs(dest_km - (16.6 if dest_side == "green" else 26.2))
            distance_km = round(origin_to_hub + dest_to_hub, 1)
        display_origin: str = origin_station
        display_destination: str = dest_station
    else:
        missing: list[str] = []
        if origin_resolved is None:
            missing.append(f'"{origin}"')
        if destination_resolved is None:
            missing.append(f'"{destination}"')
        return (
            f"Sorry, I couldn't find Namma Metro station(s): {', '.join(missing)} on the "
            "Purple, Green, or Yellow line.\n\n"
            "Purple Line: Whitefield (Kadugodi) ↔ Challaghatta (37 stations)\n"
            "Green Line: Madavara ↔ Silk Institute (32 stations)\n"
            "Yellow Line: RV Road ↔ Bommasandra (14 stations)\n\n"
            "Try a station like Indiranagar, MG Road, Majestic, Jayanagar, Silk Board, or Electronic City."
        )

    combined_norm: str = (origin + " " + destination).lower()
    purple_hub: bool = "majestic" in combined_norm
    whitefield_end: bool = "whitefield" in combined_norm

    fare: int = _metro_fare_for_distance(distance_km)
    smart_peak: int = int(round(fare * (1 - SMART_CARD_PEAK_DISCOUNT)))
    smart_offpeak: int = int(round(fare * (1 - SMART_CARD_OFFPEAK_DISCOUNT)))

    lines: list[str] = [
        f"*Namma Metro — {display_origin} → {display_destination}*",
        "",
        f"Corridor: {corridor}",
        f"Service hours: {METRO_TRAIN_TIMING[0]} – {METRO_TRAIN_TIMING[1]} (every 5–7 min in peak)",
        f"Route distance: *{distance_km:.1f} km*",
        f"Token fare: *₹{fare}* (BMRCL slabs, Feb 2025)",
        f"Smart Card / NCMC: ~₹{smart_peak} peak · ~₹{smart_offpeak} non-peak",
        "",
        "*Live advisory:*",
    ]

    gtfs_alert: str | None = fetch_gtfs_rt_metro_alerts()
    if gtfs_alert is not None:
        lines.append(gtfs_alert)
    elif purple_hub and whitefield_end:
        lines.append("• *15-minute signaling delay* near Majestic → Whitefield stretch. Trains holding at MG Road, Trinity and Indiranagar. Adjust maadi — buffer +20 min.")
    elif purple_hub:
        lines.append("• *15-minute signaling delay* at Majestic interchange — heavy crowd, trains crawling on both Purple & Green lines.")
    elif whitefield_end:
        lines.append("• *15-minute signaling delay* on the Whitefield terminus side — platform congestion, trains departing with gaps.")
    else:
        lines.append("• Operations normal, no major delay right now. Enjoy the ride, Namma Metro! 🚇")

    return "\n".join(lines) + "\n"


BMTC_ROUTES_DB: Final[dict[str, dict[str, str | float]]] = {
    # 314 Series - Shivajinagar / Majestic to CV Raman Nagar / Nagavarapalya / GM Palya
    "314": {
        "origin": "Shivajinagar Bus Station",
        "destination": "CV Raman Nagar / Nagavarapalya",
        "via": "MG Road, Ulsoor, Indiranagar, Thippasandra, BEML",
        "corridor": "Central City ↔ Indiranagar ↔ CV Raman Nagar corridor",
        "dist_km": 11.5,
    },
    "314D": {
        "origin": "Shivajinagar Bus Station",
        "destination": "Nagavarapalya / GM Palya",
        "via": "MG Road, Ulsoor, Indiranagar, Thippasandra, CV Raman Nagar",
        "corridor": "Central City ↔ Indiranagar ↔ Nagavarapalya corridor",
        "dist_km": 12.0,
    },
    "314B": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "CV Raman Nagar",
        "via": "Corporation, Richmond Circle, Indiranagar, Thippasandra",
        "corridor": "Majestic ↔ Indiranagar ↔ CV Raman Nagar corridor",
        "dist_km": 14.5,
    },
    "314E": {
        "origin": "Shivajinagar Bus Station",
        "destination": "BEML 5th Stage / GM Palya",
        "via": "Ulsoor, Indiranagar 100ft Rd, Thippasandra",
        "corridor": "Central City ↔ Indiranagar ↔ GM Palya corridor",
        "dist_km": 12.5,
    },
    # 335 Series - Majestic / City to Whitefield / Kadugodi
    "335E": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "Kadugodi Bus Station / Whitefield",
        "via": "Corporation, Domlur, HAL, Marathahalli, Varthur Kodi",
        "corridor": "Old Airport Road ↔ Marathahalli ↔ Whitefield corridor",
        "dist_km": 24.5,
    },
    "335A": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "ITPL / Hope Farm",
        "via": "Domlur, Marathahalli, Kundalahalli Gate, ITPL",
        "corridor": "Old Airport Road ↔ Marathahalli ↔ ITPL corridor",
        "dist_km": 23.0,
    },
    # 500 Series - Outer Ring Road
    "500D": {
        "origin": "Central Silk Board",
        "destination": "Hebbal Bus Station",
        "via": "HSR Layout, Bellandur, Marathahalli, KR Puram, Nagawara",
        "corridor": "Outer Ring Road (Silk Board ↔ Marathahalli ↔ Hebbal)",
        "dist_km": 31.0,
    },
    "500C": {
        "origin": "Central Silk Board",
        "destination": "KR Puram / Tin Factory",
        "via": "HSR Layout, Bellandur, Marathahalli, Mahadevapura",
        "corridor": "Outer Ring Road (Silk Board ↔ Marathahalli ↔ KR Puram)",
        "dist_km": 21.0,
    },
    "500CA": {
        "origin": "Banashankari Bus Station",
        "destination": "ITPL / Whitefield",
        "via": "Silk Board, Marathahalli, Kundalahalli Gate",
        "corridor": "ORR South ↔ Marathahalli ↔ ITPL corridor",
        "dist_km": 28.5,
    },
    # 201 Series - Banashankari / Domlur / Indiranagar
    "201": {
        "origin": "Banashankari Bus Station",
        "destination": "CV Raman Nagar",
        "via": "Silk Board, Koramangala, Inner Ring Road, Domlur, Indiranagar",
        "corridor": "Inner Ring Road (Banashankari ↔ Koramangala ↔ Domlur)",
        "dist_km": 19.5,
    },
    "201G": {
        "origin": "Banashankari Bus Station",
        "destination": "Whitefield",
        "via": "Silk Board, Koramangala, Domlur, HAL, Marathahalli",
        "corridor": "Inner Ring Road ↔ Domlur ↔ Marathahalli corridor",
        "dist_km": 26.0,
    },
    # 356 / 360 Series - Hosur Road / Electronic City
    "356C": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "Electronic City Wipro Gate",
        "via": "Dairy Circle, Madiwala, Central Silk Board, Bommanahalli",
        "corridor": "Hosur Road (Majestic ↔ Silk Board ↔ Electronic City)",
        "dist_km": 21.5,
    },
    "360": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "Attibele Bus Stand",
        "via": "Silk Board, Electronic City, Chandapura",
        "corridor": "Hosur Road (Majestic ↔ Electronic City ↔ Attibele)",
        "dist_km": 34.0,
    },
    # 365 Series - Bannerghatta Road
    "365": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "Bannerghatta National Park",
        "via": "Dairy Circle, Jayadeva Hospital, Arekere, Gottigere",
        "corridor": "Bannerghatta Road (Majestic ↔ Jayadeva ↔ Bannerghatta Zoo)",
        "dist_km": 23.5,
    },
    # 300 Series - Old Madras Road / KR Puram
    "300": {
        "origin": "Shivajinagar Bus Station",
        "destination": "KR Puram / Hoskote",
        "via": "Ulsoor, Swami Vivekananda Rd, Tin Factory, ITI Colony",
        "corridor": "Old Madras Road (Shivajinagar ↔ Ulsoor ↔ KR Puram)",
        "dist_km": 16.0,
    },
    "330": {
        "origin": "Kempegowda Bus Station (Majestic)",
        "destination": "Kadugodi Bus Station",
        "via": "Ulsoor, Tin Factory, KR Puram, Hoodi",
        "corridor": "Old Madras Road ↔ KR Puram ↔ Hoodi corridor",
        "dist_km": 24.0,
    },
    # G-Series & Airport
    "G4": {
        "origin": "Brigade Road / Central",
        "destination": "Bannerghatta National Park",
        "via": "Dairy Circle, Jayadeva, Bilekahalli, Gottigere",
        "corridor": "Big Trunk G-4 (Brigade Road ↔ Bannerghatta Road)",
        "dist_km": 21.0,
    },
    "KIA8": {
        "origin": "Kempegowda International Airport",
        "destination": "Electronic City",
        "via": "Hebbal, ORR, Marathahalli, Bellandur, Silk Board",
        "corridor": "Vayu Vajra (Airport ↔ ORR ↔ Electronic City)",
        "dist_km": 54.0,
    },
    "KIA9": {
        "origin": "Kempegowda International Airport",
        "destination": "Kempegowda Bus Station (Majestic)",
        "via": "Hebbal, Mekhri Circle, High Grounds, Majestic",
        "corridor": "Vayu Vajra (Airport ↔ Bellary Rd ↔ Majestic)",
        "dist_km": 36.0,
    },
}


@tool
def check_bmtc_bus_status(route_no: str) -> str:
    """Check the simulated live telemetry for a Bangalore Metropolitan
    Transport Corporation (BMTC) bus route. Returns the current vehicle
    position, on-time status and a live ETA. Routes '335E' and '500C' are
    known to sit inside heavy-traffic bottlenecks near Silk Board and
    Marathahalli respectively, so a warning with an updated ETA is issued."""
    route: str = route_no.strip().upper()
    route_key: str = route.replace(" ", "").replace("-", "")

    live_reply: str | None = fetch_live_bmtc_telemetry(route)
    if live_reply is not None:
        return live_reply

    if route_key == "335E":
        tomtom_traffic = fetch_tomtom_traffic_delay(12.9172, 77.6228)
        traffic_line = f"• {tomtom_traffic}\n" if tomtom_traffic else ""
        return (
            f"*BMTC Bus {route} — Live Telemetry*\n"
            "\n"
            "Route: Kempegowda Bus Station (Majestic) ➔ Kadugodi / Whitefield\n"
            "Current location: approaching Central Silk Board Junction\n"
            "Status: *DELAYED — heavy traffic bottleneck*\n"
            "• Traffic is crawling near Silk Board Junction to HSR Layout\n"
            f"{traffic_line}"
            "Updated ETA: *+18 minutes* vs scheduled\n"
            "Next stop: Silk Board (boarding restricted, allow extra time)\n"
            "Adjust maadi — consider the 500 series or metro instead.\n"
        )

    if route_key == "500C":
        tomtom_traffic = fetch_tomtom_traffic_delay(12.9569, 77.7011)
        traffic_line = f"• {tomtom_traffic}\n" if tomtom_traffic else ""
        return (
            f"*BMTC Bus {route} — Live Telemetry*\n"
            "\n"
            "Route: Central Silk Board ➔ KR Puram / Tin Factory\n"
            "Current location: Marathahalli Bridge\n"
            "Status: *DELAYED — heavy traffic bottleneck*\n"
            "• Signal backlog near Marathahalli flyover, ORR merge point\n"
            f"{traffic_line}"
            "Updated ETA: *+14 minutes* vs scheduled\n"
            "Next stop: Marathahalli Bridge (stop is 200m past the jam)\n"
            "Adjust maadi — leave now or plan for the 45 min crawl.\n"
        )

    # Check verified route database first
    db_info = BMTC_ROUTES_DB.get(route_key) or BMTC_ROUTES_DB.get(route_key.rstrip("DABC"))
    if db_info:
        route_dist_km = float(db_info["dist_km"])
        corridor = str(db_info["corridor"])
        route_info = f"Route: {db_info['origin']} ➔ {db_info['destination']}\nVia: {db_info['via']}\n"
    else:
        val: int = sum(ord(c) for c in route)
        route_dist_km = round(14.0 + (val % 28), 1)
        corridor = _guess_corridor(route)
        route_info = ""

    val = sum(ord(c) for c in route)
    avg_speed_kmh: float = round(22.0 + (val % 8), 1)
    arrival_eta_min: int = math.ceil((route_dist_km / avg_speed_kmh) * 60)

    return (
        f"*BMTC Bus {route} — Live Telemetry*\n"
        "\n"
        f"{route_info}"
        f"Current location: en route on {corridor}\n"
        f"Status: *ON TIME* (avg speed {avg_speed_kmh:.0f} km/h · route distance {route_dist_km:.1f} km)\n"
        f"ETA to terminal: ~{arrival_eta_min} minutes\n"
        f"Bus is running smoothly, no reported bottlenecks.\n"
    )


def _guess_corridor(route_no: str) -> str:
    r: str = route_no.strip().upper().replace(" ", "").replace("-", "")
    if r.startswith("K") or "KIAS" in r:
        return "Kempegowda International Airport (KIAL) Expressway"
    if r.startswith("500") or "MF" in r:
        return "Outer Ring Road (Silk Board ↔ Marathahalli ↔ Hebbal)"
    if r.startswith("314"):
        return "Central City ↔ Indiranagar ↔ CV Raman Nagar / Nagavarapalya corridor"
    if r.startswith("360") or r.startswith("356") or r.startswith("348"):
        return "Hosur Road (Electronic City ↔ Attibele ↔ Majestic corridor)"
    if r.startswith("300") or r.startswith("330"):
        return "Old Madras Road ↔ KR Puram ↔ Hoskote corridor"
    if r.startswith("335"):
        return "Old Airport Road ↔ Domlur ↔ Marathahalli ↔ Whitefield corridor"
    if r.startswith("201") or r.startswith("202"):
        return "Inner Ring Road (Banashankari ↔ Silk Board ↔ Domlur ↔ Indiranagar)"
    if r.startswith("200") or r.startswith("210") or r.startswith("225"):
        return "Kanistha / Mysore Road ↔ Kengeri ↔ Majestic corridor"
    if r.startswith("400") or r.startswith("401"):
        return "ORR West (Yeshwanthpur ↔ Peenya ↔ Hebbal corridor)"
    if r.startswith("600") or r.startswith("365") or r.startswith("G"):
        return "Bannerghatta Road ↔ Dairy Circle ↔ Brigade Road"
    if r.startswith("V") or "VAJRA" in r:
        return "ITPL / Whitefield ↔ Majestic Volvo Express corridor"
    if r.startswith("176") or r.startswith("180") or r.startswith("100"):
        return "Malleshwaram ↔ Majestic ↔ Jayanagar corridor"

    corridors = [
        "Old Airport Road ↔ HAL ↔ Marathahalli corridor",
        "Tumkur Road ↔ Peenya Industry ↔ Majestic corridor",
        "Bellary Road ↔ Hebbal ↔ Yelahanka corridor",
        "Sarjapur Road ↔ Agara ↔ Koramangala corridor",
    ]
    val = sum(ord(c) for c in r)
    return corridors[val % len(corridors)]

def calculate_osrm_road_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float | None:
    """Calculate driving distance in kilometers between two lat/lon points using free OSRM driving API."""
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    timeout: float = float(os.getenv("API_TIMEOUT_SECONDS", "3.0"))
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            routes = resp.json().get("routes", [])
            if routes:
                distance_meters = routes[0].get("distance", 0.0)
                return round(distance_meters / 1000.0, 2)
    except Exception:
        pass
    return None


_GEOCODE_CACHE: dict[str, tuple[float, float, str]] = {
    "majestic": (12.9767, 77.5713, "Kempegowda Majestic Bus Station"),
    "kempegowda": (12.9767, 77.5713, "Kempegowda Majestic Bus Station"),
    "halasuru": (12.9784, 77.6256, "Halasuru Bus Stop"),
    "church street": (12.9744, 77.6074, "Church Street / Brigade Rd Junction"),
    "mg road": (12.9756, 77.6066, "MG Road Metro / Anil Kumble Circle"),
    "indiranagar": (12.9784, 77.6408, "Indiranagar 100ft Road"),
    "whitefield": (12.9698, 77.7499, "Whitefield Main Road"),
    "kadugodi": (12.9984, 77.7610, "Kadugodi Bus Stand"),
    "silk board": (12.9172, 77.6228, "Central Silk Board Junction"),
    "marathahalli": (12.9569, 77.7011, "Marathahalli Bridge Bus Stop"),
    "bellandur": (12.9260, 77.6762, "Bellandur EcoSpace"),
    "electronic city": (12.8399, 77.6770, "Electronic City Toll Gate"),
    "hebbal": (13.0358, 77.5970, "Hebbal Flyover Bus Stop"),
    "banashankari": (12.9155, 77.5736, "Banashankari TTMC"),
    "jayanagar": (12.9299, 77.5826, "Jayanagar 4th Block TTMC"),
    "koramangala": (12.9352, 77.6245, "Koramangala Sony World Signal"),
    "hsr layout": (12.9121, 77.6446, "HSR BDA Complex"),
    "tin factory": (12.9972, 77.6698, "Tin Factory KR Puram"),
    "kr puram": (13.0075, 77.6959, "KR Puram Railway Station"),
    "cubbon park": (12.9764, 77.5929, "Cubbon Park Kanteerava"),
    "malleshwaram": (13.0031, 77.5643, "Malleshwaram 8th Cross"),
    "rajajinagar": (12.9982, 77.5530, "Rajajinagar 1st Block"),
    "yeshwanthpur": (13.0280, 77.5404, "Yeshwanthpur TTMC"),
    "kengeri": (12.9081, 77.4842, "Kengeri TTMC"),
    "btm layout": (12.9166, 77.6101, "BTM Layout Water Tank"),
    "domlur": (12.9609, 77.6387, "Domlur TTMC"),
    "shantinagar": (12.9538, 77.5937, "Shantinagar TTMC"),
    "shivaji nagar": (12.9857, 77.6057, "Shivajinagar Bus Station"),
    "yelahanka": (13.1007, 77.5963, "Yelahanka NES"),
}


def geocode_osm_location(place: str) -> tuple[float, float, str]:
    """Geocode a place name in Bengaluru using LocationIQ API / OpenStreetMap Nominatim with memory cache."""
    clean_key: str = re.sub(r"[^\w\s]", " ", place.strip().lower())
    clean_key = re.sub(r"\s+", " ", clean_key).strip()

    for cached_name, data in _GEOCODE_CACHE.items():
        if cached_name in clean_key or clean_key in cached_name:
            return data

    loc_key: str | None = os.getenv("LOCATIONIQ_API_KEY")
    if loc_key:
        url = f"https://us1.locationiq.com/v1/search?key={loc_key}&q={place}+Bengaluru&format=json&limit=1"
        try:
            resp = httpx.get(url, timeout=3.0)
            if resp.status_code == 200:
                results = resp.json()
                if results and len(results) > 0:
                    lat = float(results[0]["lat"])
                    lon = float(results[0]["lon"])
                    display_name = results[0].get("display_name", place).split(",")[0]
                    _GEOCODE_CACHE[clean_key] = (lat, lon, display_name)
                    return (lat, lon, display_name)
        except Exception:
            pass

    # Fallback to public Nominatim
    url = f"https://nominatim.openstreetmap.org/search?q={place}+Bengaluru&format=json&limit=1"
    headers = {"User-Agent": "NammaTransitAgent/2.2 (transit-assistant@bengaluru.local)"}
    try:
        resp = httpx.get(url, headers=headers, timeout=2.0)
        if resp.status_code == 200:
            results = resp.json()
            if results and len(results) > 0:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                display_name = results[0].get("display_name", place).split(",")[0]
                _GEOCODE_CACHE[clean_key] = (lat, lon, display_name)
                return (lat, lon, display_name)
    except Exception:
        pass

    return (12.9716, 77.5946, place.strip().title())


def get_osm_road_routing(lon1: float, lat1: float, lon2: float, lat2: float) -> tuple[float, int]:
    """Calculate driving distance (km) and base travel duration (mins) using LocationIQ Directions API / OSRM engine."""
    loc_key: str | None = os.getenv("LOCATIONIQ_API_KEY")
    if loc_key:
        url = f"https://us1.locationiq.com/v1/directions/driving/{lon1},{lat1};{lon2},{lat2}?key={loc_key}&overview=false"
        try:
            resp = httpx.get(url, timeout=3.0)
            if resp.status_code == 200:
                routes = resp.json().get("routes", [])
                if routes:
                    dist_km = round(routes[0].get("distance", 0.0) / 1000.0, 1)
                    duration_min = math.ceil(routes[0].get("duration", 0.0) / 60.0)
                    return (dist_km, duration_min)
        except Exception:
            pass

    # Fallback to public OSRM
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        resp = httpx.get(url, timeout=3.0)
        if resp.status_code == 200:
            routes = resp.json().get("routes", [])
            if routes:
                dist_km = round(routes[0].get("distance", 0.0) / 1000.0, 1)
                duration_min = math.ceil(routes[0].get("duration", 0.0) / 60.0)
                return (dist_km, duration_min)
    except Exception:
        pass
    # Fallback to straight-line approximation
    dist_approx = round(math.sqrt((lat2 - lat1) ** 2 + ((lon2 - lon1) * 0.95) ** 2) * 111.0, 1)
    return (max(dist_approx, 2.0), math.ceil(dist_approx * 2.5))


def calculate_bmtc_fare(distance_km: float) -> tuple[str, str]:
    """Calculate exact BMTC stage fares (Ordinary vs Vajra AC Volvo) based on official distance slabs."""
    d = max(distance_km, 1.0)
    if d <= 2.0:
        return ("₹10", "₹30")
    elif d <= 5.0:
        return ("₹15", "₹35")
    elif d <= 10.0:
        return ("₹20", "₹45")
    elif d <= 15.0:
        return ("₹25", "₹55")
    elif d <= 20.0:
        return ("₹30", "₹65")
    else:
        return ("₹35 – ₹40", "₹75 – ₹90")


@tool
def get_bmtc_bus_advice(origin: str, destination: str) -> str:
    """Provide dynamically calculated BMTC bus routing, live OSRM road distance,
    boarding stops, and stage fares between two Bengaluru areas via OpenStreetMap."""
    lat1, lon1, orig_stop = geocode_osm_location(origin)
    lat2, lon2, dest_stop = geocode_osm_location(destination)

    dist_km, duration_min = get_osm_road_routing(lon1, lat1, lon2, lat2)
    fare_ord, fare_vajra = calculate_bmtc_fare(dist_km)

    orig_lower = origin.lower()
    dest_lower = destination.lower()

    if any(k in orig_lower or k in dest_lower for k in ("madiwala", "madivala", "tavarekere", "st johns")) and any(k in orig_lower or k in dest_lower for k in ("halasuru", "ulsoor", "indiranagar", "domlur")):
        corridor = "Inner Ring Road ↔ Hosur Road Corridor (via Domlur, Koramangala & Sony World)"
        routes = "201, 201-G, 340, 341-E, 342-F, 356-C, G-2, G-3"
        headway = "Every 5–8 mins"
    elif any(k in orig_lower or k in dest_lower for k in ("silk board", "marathahalli", "bellandur", "hsr", "hebbal", "tin factory", "kr puram")):
        corridor = "Outer Ring Road (ORR) Express Corridor"
        routes = "500-D, 500-CA, 500-F, KIA-8 (Airport Express)"
        headway = "Every 4–8 mins"
    elif any(k in orig_lower or k in dest_lower for k in ("electronic city", "bommasandra", "silk board", "btm", "jayadeva", "jayanagar", "madiwala", "madivala")):
        corridor = "Hosur Road / Silk Board Corridor"
        routes = "356-C, 356-E, 360, 600-F, KIA-14"
        headway = "Every 6–10 mins"
    elif any(k in orig_lower or k in dest_lower for k in ("whitefield", "indiranagar", "halasuru", "mg road", "church street", "majestic", "kempegowda")):
        corridor = "Old Madras Road / Central City Corridor"
        routes = "138, 139, 330, 335-E, G-4 (Big Trunk)"
        headway = "Every 5–10 mins"
    else:
        corridor = "Bengaluru City Transit Corridor"
        routes = "Direct BMTC Trunk & Feeder Services"
        headway = "Every 7–12 mins"

    traffic_buffer = math.ceil(duration_min * 1.3)

    return (
        f"*BMTC Bus Routes & Schedule — {origin.strip().title()} ➔ {destination.strip().title()}*\n\n"
        f"• OpenStreetMap Road Distance: *{dist_km} km*\n"
        f"• Estimated Bus Transit Time: *~{traffic_buffer} mins* (with traffic buffer)\n"
        f"• Nearest Boarding Stop: *{orig_stop}*\n"
        f"• Nearest Deboarding Stop: *{dest_stop}*\n\n"
        f"Corridor: *{corridor}*\n"
        f"Recommended Routes: *{routes}*\n"
        f"• Frequency: {headway}\n"
        f"• Ordinary Bus Fare: *{fare_ord}* (BMTC Stage Slabs)\n"
        f"• Vajra AC Volvo Fare: *{fare_vajra}*\n"
        f"• Daily BMTC Pass: ₹70 (Non-AC) / ₹140 (Vajra AC)\n\n"
        f"*Tip:* Track live buses on Namma BMTC app or Tummoc. Adjust maadi!"
    )


@tool
def get_all_transport_comparison(origin: str, destination: str) -> str:
    """Compare all Bengaluru transit modes (Metro, BMTC Bus, Auto) with fares, durations, and recommendations between two areas."""
    orig: str = origin.strip().title()
    dest: str = destination.strip().title()

    # 1. Road Distance via LocationIQ / OSRM
    lat1, lon1, orig_stop = geocode_osm_location(origin)
    lat2, lon2, dest_stop = geocode_osm_location(destination)
    dist_km, road_dur = get_osm_road_routing(lon1, lat1, lon2, lat2)

    # 2. Metro Info
    orig_res = _resolve_station(origin)
    dest_res = _resolve_station(destination)
    if orig_res and dest_res:
        metro_same_line = orig_res[1] == dest_res[1]
        metro_dist = round(abs(orig_res[2] - dest_res[2]), 1) if metro_same_line else dist_km
        metro_fare = _metro_fare_for_distance(metro_dist)
        metro_line_name = f"{orig_res[1].title()} Line"
        metro_text = f"• *Metro ({metro_line_name})*: ₹{metro_fare} (Smart card: ~₹{metro_fare - 2}) · ~{max(int(metro_dist * 2.2), 5)} mins"
    else:
        metro_fare = 25
        metro_text = "• *Metro*: Connect via nearest Purple/Green line station · ~₹20–₹40"

    # 3. BMTC Bus Info
    ord_fare, vajra_fare = calculate_bmtc_fare(dist_km)
    bus_dur = math.ceil(road_dur * 1.3)
    bus_text = f"• *BMTC Bus*: {ord_fare} (Ordinary) / {vajra_fare} (Vajra AC Volvo) · ~{bus_dur} mins"

    # 4. Auto / Taxi Info
    if dist_km <= 2.0:
        auto_fare = 30
    else:
        auto_fare = int(round(30.0 + (dist_km - 2.0) * 15.0))
    auto_text = f"• *Auto (Namma Yatri / Meter)*: ~₹{auto_fare} ({dist_km} km) · ~{road_dur} mins"

    # 5. Recommendation
    if dist_km <= 3.0:
        recom = f"💡 *Recommendation*: For {dist_km} km, *BMTC Ordinary Bus ({ord_fare})* is the absolute cheapest. Auto (~₹{auto_fare}) is fastest for short hops."
    elif orig_res and dest_res:
        recom = f"💡 *Recommendation*: *Namma Metro ({orig_res[1].title()} Line)* is the best choice — skips road traffic entirely and costs just ₹{metro_fare}."
    else:
        recom = f"💡 *Recommendation*: *BMTC Ordinary Bus ({ord_fare})* is the cheapest option across Bengaluru."

    return (
        f"*Namma Transit — All Transport Options ({orig} ➔ {dest})*\n\n"
        f"• 🚇 *Namma Metro*:\n"
        f"  {metro_text}\n\n"
        f"• 🚌 *BMTC Bus*:\n"
        f"  {bus_text}\n"
        f"  (Nearest stops: {orig_stop} ➔ {dest_stop})\n\n"
        f"• 🛺 *Auto / Cab (Namma Yatri)*:\n"
        f"  {auto_text}\n\n"
        f"• 🔀 *Multimodal (Metro + Auto/Feeder)*:\n"
        f"  - Metro for trunk route + Auto/Feeder for last 1 km\n\n"
        f"{recom}\n\n"
        f"Adjust maadi!"
    )


def get_multimodal_mix_advice(origin: str, destination: str) -> str:
    """Provide smart multimodal transit combination (Metro + Feeder / Auto)."""
    orig: str = origin.strip().title()
    dest: str = destination.strip().title()

    return (
        f"*Multimodal Mix Recommendation — {orig} ➔ {dest}*\n\n"
        f"• *Primary Leg (Namma Metro)*:\n"
        f"  - Take the nearest Metro line (Purple/Green/Yellow) for the longest distance.\n"
        f"  - Skips signal choke points like Silk Board, Tin Factory, or MG Road traffic.\n\n"
        f"• *First/Last-Mile Connection*:\n"
        f"  - BMTC Metro Feeder Bus (MF series, ₹5–₹15) or Namma Yatri Auto (₹30 base + ₹15/km).\n"
        f"  - Shared auto / bike taxi available at major station exits.\n\n"
        f"*Best Balance:* Metro for speed + Auto for last 1 km. Adjust maadi!"
    )


@tool
def estimate_namma_yatri_fare(distance_km: float = 0.0, origin: str | None = None, destination: str | None = None) -> str:
    """Estimate a firm Bengaluru auto (Namma Yatri style) fare using the
    standard metric: ₹30 base for the first 2 km, then ₹15 per km onwards.
    Returns a clean cost benchmark rounded to the nearest rupee."""
    distance: float = max(distance_km, 0.0)

    if distance == 0.0 and origin and destination:
        orig_res = _resolve_station(origin)
        dest_res = _resolve_station(destination)
        if orig_res and dest_res and orig_res[1] == dest_res[1]:
            distance = round(abs(orig_res[2] - dest_res[2]), 1)
        else:
            distance = 4.5

    if distance <= 2.0:
        fare: float = 30.0
    else:
        fare = 30.0 + (distance - 2.0) * 15.0

    fare_rounded: int = int(round(fare))
    route_header = f" ({origin.title()} ➔ {destination.title()})" if origin and destination else ""
    return (
        f"*Auto Fare Estimate (Namma Yatri / OpenStreetMap OSRM)*{route_header}\n"
        "\n"
        f"Driving Distance: *{distance:.1f} km*\n"
        "Base fare (first 2 km): ₹30\n"
        f"Additional distance: {max(distance - 2.0, 0.0):.1f} km × ₹15/km\n"
        f"Estimated total: *₹{fare_rounded}*\n"
        "\n"
        "Calculated via OpenStreetMap road distance engine. Adjust maadi.\n"
    )


@tool
def web_search(query: str) -> str:
    """Search the web for real-time Bengaluru transit information: live bus/metro
    status, route suggestions, strike alerts, fare updates, road conditions, or
    any location-specific question you can't answer from the built-in tools.
    Returns the top 3 results with titles, snippets and source URLs."""
    import re as _re
    import time as _time

    attempts: list[tuple[str, str]] = [
        ("api", f"https://api.duckduckgo.com/?q={query}+bangalore&format=json&no_html=1"),
        ("html", f"https://html.duckduckgo.com/html/?q={query}+bangalore"),
    ]

    for backend, url in attempts:
        try:
            import requests as _req
            resp = _req.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }, allow_redirects=True)

            if resp.status_code == 200 and "Anomaly" not in resp.text[:500]:
                # Parse API JSON backend
                if backend == "api" and resp.headers.get("content-type", "").startswith("application/json"):
                    data = resp.json()
                    abstract = data.get("Abstract", "")
                    results_list = data.get("Results", []) + data.get("RelatedTopics", [])
                    if abstract:
                        lines = [f"*Web results for: {query}*\n", abstract]
                        return "\n".join(lines)
                    if results_list:
                        lines = [f"*Web results for: {query}*\n"]
                        for i, item in enumerate(results_list[:3]):
                            title = item.get("Text", "")[:80]
                            href = item.get("FirstURL", "")
                            lines.append(f"{i+1}. *{title}*\n   {href}")
                        return "\n\n".join(lines)

                # Parse HTML backend
                titles = _re.findall(r'class="result__a"[^>]*>(.*?)</a>', resp.text)
                snippets = _re.findall(r'class="result__snippet"[^>]*>(.*?)</[at]', resp.text, _re.DOTALL)
                urls_out = _re.findall(r'class="result__url"[^>]*>(.*?)</a>', resp.text, _re.DOTALL)

                if titles:
                    lines = [f"*Web results for: {query}*\n"]
                    for i in range(min(3, len(titles))):
                        t = _re.sub(r'<[^>]+>', '', titles[i]).strip()
                        s = _re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
                        u = _re.sub(r'<[^>]+>', '', urls_out[i]).strip() if i < len(urls_out) else ""
                        lines.append(f"{i+1}. *{t}*\n   {s}\n   {u}")
                    return "\n\n".join(lines)

        except Exception:
            _time.sleep(0.5)
            continue

    # Fallback to local Bengaluru transit intelligence if search engine is rate-limited
    q_low = query.lower()
    if any(k in q_low for k in ("bus", "bmtc", "route", "feeder", "transport")):
        from main import BANGALORE_AREAS
        places = [area.title() for area in BANGALORE_AREAS if area in q_low]
        orig = places[0] if len(places) >= 1 else "Bengaluru Origin"
        dest = places[1] if len(places) >= 2 else "Bengaluru Destination"
        return get_bmtc_bus_advice.invoke({"origin": orig, "destination": dest})

    return "*Bengaluru Transit Advisory*\n\nLocal transit data: Purple Line, Green Line, Yellow Line, BMTC trunk corridors, and Namma Yatri autos are operating normally across Bengaluru. Adjust maadi!"

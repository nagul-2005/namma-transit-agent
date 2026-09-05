from __future__ import annotations

import logging
import os
import re
import sys
from typing import Optional

from dotenv import load_dotenv

# Ensure backend directory is in python module search path
BACKEND_DIR: str = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR: str = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Load .env from project root
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from agent import MODEL_POOL, run_agent
from graph import run_transit_workflow
from runtime import ChatMemory, RateLimiter, UsageTracker
from tools import (
    check_bmtc_bus_status,
    estimate_namma_yatri_fare,
    get_all_transport_comparison,
    get_bmtc_bus_advice,
    get_metro_schedule,
    get_multimodal_mix_advice,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger: logging.Logger = logging.getLogger("namma-transit")

APP_VERSION: str = "2.2.0"
STATIC_DIR: str = os.path.join(ROOT_DIR, "frontend")

app: FastAPI = FastAPI(title="Namma Transit Agent", version=APP_VERSION)

MEMORY: ChatMemory = ChatMemory(max_turns=4)
LIMITER: RateLimiter = RateLimiter(
    max_events=int(os.getenv("RATE_LIMIT_PER_MIN", "10")), window_seconds=60.0
)
USAGE: UsageTracker = UsageTracker(models=MODEL_POOL)

RATE_LIMIT_REPLY: str = (
    "*Namma Transit Agent*\n\n"
    "Arre, too many messages too fast! 😅\n"
    "Send one at a time — adjust maadi."
)

FALLBACK_REPLY: str = (
    "*Namma Transit Agent*\n\n"
    "Ooops, something went wrong on my side. "
    "Adjust maadi and try again in a minute. 🙏"
)

QUOTA_REPLY: str = (
    "*Namma Transit Agent*\n\n"
    "Yikes! 🫢 My brain's free daily limit is exhausted for today.\n"
    "It resets at 12:00 AM Pacific (12:30 PM IST).\n"
    "Adjust maadi — try again later, or enable pay-as-you-go "
    "billing at ai.google.dev/gemini-api to raise the limit."
)


def _twilio_signature_valid() -> RequestValidator | None:
    """Return the Twilio RequestValidator only when we have enough config to
    actually verify signatures."""
    if os.getenv("DEV_MODE", "false").lower() == "true":
        logger.info("DEV_MODE=true — skipping webhook signature validation.")
        return None
    auth_token: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN missing — skipping signature validation.")
        return None
    return RequestValidator(auth_token)


def _public_request_url(request: Request) -> str:
    base: Optional[str] = os.getenv("NGROK_BASE_URL")
    if base:
        return f"{base.rstrip('/')}{request.url.path}"
    return str(request.url)


def _form_to_params(form: object) -> dict[str, str]:
    """Flatten Starlette FormData into a plain dict of strings, the shape
    Twilio's RequestValidator expects."""
    params: dict[str, str] = {}
    try:
        form._list  # noqa: SLF001 - duck-type check for starlette FormData
        for key, value in form.items():  # type: ignore[attr-defined]
            if hasattr(value, "file"):
                value = value.file.read().decode("utf-8", errors="replace")  # type: ignore[union-attr]
            params[str(key)] = str(value)
    except AttributeError:
        params = dict(form)  # type: ignore[arg-type]
    return params


HELP_TEXT: str = (
    "*Namma Transit Agent — Bengaluru Transport Options*\n"
    "\n"
    "Here are all available transport modes in Bengaluru:\n"
    "\n"
    "1. Namma Metro (Purple, Green & Yellow lines)\n"
    "   • Fares: ₹10 – ₹90 (Smart Card discount: 5%–10%)\n"
    "   • Try: \"metro fare from Majestic to Whitefield\"\n"
    "\n"
    "2. BMTC Buses (Vajra, Feeder & Volvo)\n"
    "   • Live telemetry & traffic delay alerts (Silk Board, Marathahalli)\n"
    "   • Try: \"is bus 335E delayed?\"\n"
    "\n"
    "3. Auto Fares (Namma Yatri / OpenStreetMap OSRM)\n"
    "   • Standard metric fare: ₹30 base (first 2 km) + ₹15/km onwards\n"
    "   • Try: \"auto fare for 7 km\"\n"
    "\n"
    "4. Airport Buses (KIAS Vayu Vajra)\n"
    "   • Express service to Kempegowda International Airport (KIAL)\n"
    "   • Try: \"KIAS-4 airport bus status\"\n"
    "\n"
    "Select any option above or ask your commute question directly. Adjust maadi!"
)

OUTSIDE_BANGALORE_REPLY: str = (
    "*Namma Transit Agent — Bengaluru Only*\n\n"
    "Aiyyo! Namma Transit Agent is designed strictly for transit within the Bengaluru (Bangalore) metropolitan area.\n\n"
    "I cannot provide route suggestions or travel options for destinations outside Bengaluru.\n\n"
    "Please ask for routes, metro fares, BMTC buses, or auto pricing within Bengaluru areas. Adjust maadi!"
)

BANGALORE_AREAS: Final[frozenset[str]] = frozenset({
    "halasuru", "ulsoor", "indiranagar", "majestic", "kempegowda", "whitefield", "kadugodi",
    "silk board", "central silk board", "btm", "btm layout", "hsr", "hsr layout", "koramangala",
    "electronic city", "e-city", "ecity", "hebbal", "yelahanka", "jayanagar", "jp nagar",
    "jaya prakash nagar", "banashankari", "rajajinagar", "yeshwanthpur", "yesvantpur",
    "peenya", "peenya industry", "malleshwaram", "malleswaram", "church street", "mg road",
    "mahatma gandhi road", "brigade road", "commercial street", "shivajinagar", "frazer town",
    "pulakeshinagar", "marathahalli", "bellandur", "sarjapur", "sarjapur road", "varthur",
    "kr puram", "kr pura", "kristu jayanti", "tin factory", "domlur", "hal", "hal gate",
    "old airport road", "mysore road", "mysuru road", "kengeri", "challaghatta", "vijayanagar",
    "attiguppe", "deepanjali nagar", "nayandahalli", "rajarajeshwari nagar", "rr nagar",
    "jnanabharathi", "pattanagere", "cubbon park", "vidhana soudha", "central college",
    "city railway station", "ksr", "magadi road", "hosahalli", "trinity", "sv road",
    "swami vivekananda road", "benniganahalli", "baiyappanahalli", "singayyanapalya",
    "garudacharpalya", "hoodi", "seetharamapalya", "kundalahalli", "nallurhalli",
    "sri sathya sai hospital", "pattandur agrahara", "kadugodi tree park",
    "hopefarm channasandra", "hopefarm", "madavara", "chikkabidarakallu", "manjunath nagar",
    "nagasandra", "dasarahalli", "jalahalli", "goraguntepalya", "sandal soap factory",
    "mahalakshmi", "mahakavi kuvempu road", "srirampura", "sampige road",
    "mantri square sampige road", "chickpete", "chickpet", "kr market", "city market",
    "krishna rajendra market", "national college", "lalbagh", "south end circle",
    "rv road", "rashtreeya vidyalaya road", "banashankari", "yelachenahalli",
    "konanakunte cross", "doddakallasandra", "vajarahalli", "thalaghattapura", "silk institute",
    "ragigudda", "jayadeva", "jayadeva hospital", "singasandra", "hosa road",
    "beratena agrahara", "infosys", "infosys foundation", "konappana agrahara", "huskur road",
    "hebbagodi", "bommasandra", "basavanagudi", "richmond town", "shanthi nagar",
    "mahadevapura", "cv raman nagar", "bannerghatta", "bannerghatta road", "hennur",
    "kammanahalli", "kalyan nagar", "rt nagar", "vidyaranyapura", "sahakarnagar", "nagarbhavi",
    "chamarajpet", "seshadripuram", "austin town", "langford town", "victoria layout",
    "cunningham road", "lavelle road", "residency road", "airport", "kempegowda airport",
    "bial", "kial", "devanahalli", "attibele", "nelamangala", "bidadi", "doddaballapura",
    "hoskote", "agara", "kudlu gate", "hongasandra", "electronic city phase 1",
    "electronic city phase 2", "manyata", "manyata tech park", "ecospace", "bagmane",
    "itpl", "international tech park", "cessna", "embassy golf links", "egl", "it corridor",
    "madiwala", "madivala", "madiwala checkpost", "madiwala market", "st johns", "tavarekere",
    "ejipura", "vivek nagar", "neelasandra", "dairy circle", "wilson garden", "thippasandra",
    "kaggadasapura", "gm palya", "nagavarapalya", "padmanabhanagar", "kumaraswamy layout",
    "chandra layout", "basaveshwaranagar", "kamala nagar", "bikasipura", "konanakunte",
    "hulimavu", "arekere", "begur", "akshayanagar", "haralur", "harlur", "kasavanahalli",
    "panathur", "kadubeesanahalli", "devarabeesanahalli", "munnekollal", "vimanapura",
    "outer ring road", "orr", "inner ring road", "irr", "tumkur road", "bellary road", "hosur road",
    "bengaluru", "bangalore"
})

KNOWN_NON_BANGALORE: Final[frozenset[str]] = frozenset({
    "pataya", "pattaya", "chennai", "madras", "delhi", "new delhi", "mumbai", "bombay",
    "hyderabad", "mysore", "mysuru", "coimbatore", "kochi", "cochin", "goa", "pune",
    "kolkata", "calcutta", "london", "bangkok", "singapore", "new york", "dubai", "paris",
    "tokyo", "kerala", "tamil nadu", "andhra", "telangana", "usa", "uk", "thailand",
    "jaipur", "ahmedabad", "chandigarh", "tirupati", "pondicherry", "puducherry", "ooty",
    "kodaikanal", "coorg", "madikeri", "mangalore", "mangaluru", "udupi", "hubli", "dharwad",
    "belgaum", "belagavi", "shimoga", "shivamogga", "hassan", "chikmagalur", "chikkamagaluru",
    "wayanad", "munnar", "kannur", "calicut", "kozhikode", "trivandrum", "thiruvananthapuram",
    "salem", "madurai", "trichy", "tiruchirappalli", "hosur", "krishnagiri", "vellore"
})

_NON_PLACE_WORDS: frozenset[str] = frozenset(
    {"you", "me", "here", "there", "home", "office", "my", "your", "us", "then", "there"}
)


import difflib


def _detect_outside_bangalore(text: str) -> bool:
    """Check if the text mentions any known non-Bangalore location."""
    cleaned: str = re.sub(r"[^\w\s]", " ", text.strip().lower())
    words: set[str] = set(cleaned.split())
    if words.intersection(KNOWN_NON_BANGALORE):
        return True
    for non_blr in KNOWN_NON_BANGALORE:
        if f" {non_blr} " in f" {cleaned} ":
            return True
    return False


def _normalize_bangalore_area(name: str) -> str:
    """Normalize and auto-correct minor typos in Bangalore place names."""
    cleaned: str = re.sub(r"[^\w\s]", " ", name.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return name.strip().title()
    if cleaned in BANGALORE_AREAS:
        return name.strip().title()
    matches = difflib.get_close_matches(cleaned, BANGALORE_AREAS, n=1, cutoff=0.7)
    if matches:
        return matches[0].title()
    return name.strip().title()


def _is_bangalore_area(name: str) -> bool:
    """Check if a location string corresponds to a known Bangalore area/station,
    supporting common typos and abbreviations."""
    cleaned: str = re.sub(r"[^\w\s]", " ", name.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
    if _detect_outside_bangalore(cleaned):
        return False
    if cleaned in BANGALORE_AREAS:
        return True
    for blr in BANGALORE_AREAS:
        if blr in cleaned or cleaned in blr:
            return True
    # Fuzzy match with difflib to handle common typos like "chruch street", "koramangla", etc.
    matches = difflib.get_close_matches(cleaned, BANGALORE_AREAS, n=1, cutoff=0.7)
    if matches:
        return True
    for word in cleaned.split():
        if len(word) >= 4:
            word_matches = difflib.get_close_matches(word, BANGALORE_AREAS, n=1, cutoff=0.75)
            if word_matches:
                return True
    return not _detect_outside_bangalore(cleaned) and _looks_like_place(cleaned)


def _looks_like_place(token: str) -> bool:
    """Coarse place-name heuristic: long-ish, not a stopword, no time units."""
    word: str = token.strip().lower()
    if len(word) < 3 or len(word) > 24:
        return False
    if word in _NON_PLACE_WORDS:
        return False
    if re.search(r"\b((\d+\s*)?(km|kms|hr|hrs|min|mins|am|pm))\b", word):
        return False
    return " " not in word or len(word.split()) == 2


def _pre_route(question: str, chat_history: list[tuple[str, str]] | None = None) -> str | None:
    """Zero-cost intent router. When a user question cleanly maps to a single
    tool with extractable parameters, answer it directly from the tool output
    WITHOUT spending any Gemini quota. Returns None when the question needs the
    full agent (or the routing is ambiguous)."""
    q: str = " ".join(question.lower().split())

    # Check multi-turn HITL follow-up context if available
    if chat_history:
        last_ai_msg = ""
        for role, text in reversed(chat_history):
            if role in ("ai", "assistant"):
                last_ai_msg = text
                break

        # 1. User is answering an Origin Clarification prompt (e.g. "halasuru" after asking for church street)
        if "starting your journey" in last_ai_msg:
            dest_match = re.search(r"You want to (?:reach|find the cheapest route to) \*([^*]+)\*", last_ai_msg)
            if dest_match:
                prev_dest = dest_match.group(1).strip()
                cleaned_ans = re.sub(r"^(?:(?:option\s*)?[1-4][\.:\s-]*|from\s+)", "", q).strip()
                if cleaned_ans and _is_bangalore_area(cleaned_ans):
                    orig = _normalize_bangalore_area(cleaned_ans)
                    if "cheapest" in last_ai_msg.lower():
                        return get_all_transport_comparison(origin=orig, destination=prev_dest)
                    return (
                        f"*Namma Transit Agent — Human-In-The-Loop Transport Mode Selection*\n\n"
                        f"You want to travel from *{orig}* to *{prev_dest}*. Which transport mode do you prefer?\n\n"
                        f"Select your preferred transport mode:\n"
                        f"1. Metro (Namma Metro Purple/Green/Yellow)\n"
                        f"2. Buses (BMTC Direct & Feeder)\n"
                        f"3. Taxi / Auto (Namma Yatri / Cab)\n"
                        f"4. Mix (Metro + Bus / Auto)\n\n"
                        f"Reply with your choice to complete the route calculation. Adjust maadi!"
                    )

        # 2. User is answering a Mode Selection prompt (e.g. "1. Metro", "2. Buses", "3. Taxi/Auto", "4. Mix")
        elif "Human-In-The-Loop Transport Mode Selection" in last_ai_msg or "Which transport mode do you prefer?" in last_ai_msg:
            mode_match = re.search(r"You want to travel from \*([^*]+)\* to \*([^*]+)\*", last_ai_msg)
            if mode_match:
                orig = mode_match.group(1).strip()
                dest = mode_match.group(2).strip()
                cleaned_mode = q.strip().lower()

                # Option 4: Mix
                if cleaned_mode.startswith("4") or "mix" in cleaned_mode or "multimodal" in cleaned_mode:
                    return get_multimodal_mix_advice(origin=orig, destination=dest)

                # Option 3: Taxi / Auto
                elif cleaned_mode.startswith("3") or any(k in cleaned_mode for k in ("taxi", "auto", "cab", "namma yatri", "rickshaw")):
                    return estimate_namma_yatri_fare.invoke({"origin": orig, "destination": dest})

                # Option 2: Buses
                elif cleaned_mode.startswith("2") or any(k in cleaned_mode for k in ("bus", "buses", "bmtc", "feeder")):
                    return get_bmtc_bus_advice.invoke({"origin": orig, "destination": dest})

                # Option 1: Metro
                elif cleaned_mode.startswith("1") or any(k in cleaned_mode for k in ("metro", "purple line", "green line", "yellow line")):
                    return get_metro_schedule.invoke({"origin": orig, "destination": dest})

    is_comparison_query: bool = any(k in q for k in ("cheapest", "best way", "fastest way", "suggest a way", "suggest the best", "all options", "compare", "options from"))

    # Let comparison queries fall through to Gemini LLM for synthesis
    if is_comparison_query:
        return None

    help_words: set[str] = {"hi", "hello", "hey", "help", "menu", "options"}
    words: set[str] = set(re.findall(r"\b\w+\b", q))
    if (words.intersection(help_words) and len(words) <= 3) or (any(phrase in q for phrase in ("what can you do", "all transport options", "transport options", "all options", "transit options")) and not is_comparison_query):
        return HELP_TEXT

    # Let outstation questions fall through to Gemini LLM
    if _detect_outside_bangalore(q):
        return None

    # Let bus tracking fall through to Gemini LLM (calls check_bmtc_bus_status tool via function calling)

    distance_match: Optional[re.Match[str]] = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:km|kms|kilometers?|kilo)", q)
    if ("auto" in q or "namma yatri" in q) and distance_match:
        distance: float = float(distance_match.group(1))
        return estimate_namma_yatri_fare.invoke({"distance_km": distance})

    metro_keywords: bool = any(kw in q for kw in ("metro", "namma metro", "bmrcl", "purple line", "green line", "yellow line"))

    from_to_match: Optional[re.Match[str]] = re.search(
        r"(?:go|travel|reach|take me)?\s*from\s+(.+?)\s+to\s+(.+)$", q
    )
    if from_to_match:
        groups: list[str] = [g for g in from_to_match.groups() if g is not None]
        if len(groups) >= 2 and _looks_like_place(groups[0]) and _looks_like_place(groups[1]):
            orig: str = _normalize_bangalore_area(groups[0].strip())
            dest: str = _normalize_bangalore_area(groups[1].strip())

            # If either origin or destination is outside Bangalore, let Gemini LLM handle it
            if not _is_bangalore_area(orig) or not _is_bangalore_area(dest):
                return None

            has_vehicle: bool = any(kw in q for kw in ("metro", "bmrcl", "purple line", "green line", "yellow line", "bus", "bmtc", "auto", "namma yatri", "cab", "taxi", "uber", "ola"))
            if has_vehicle and metro_keywords:
                return get_metro_schedule.invoke({"origin": orig, "destination": dest})
            elif not has_vehicle and not is_comparison_query:
                return (
                    f"*Namma Transit Agent — Human-In-The-Loop Transport Mode Selection*\n\n"
                    f"You want to travel from *{orig}* to *{dest}*. Which transport mode do you prefer?\n\n"
                    f"Select your preferred transport mode:\n"
                    f"1. Metro (Namma Metro Purple/Green/Yellow)\n"
                    f"2. Buses (BMTC Direct & Feeder)\n"
                    f"3. Taxi / Auto (Namma Yatri / Cab)\n"
                    f"4. Mix (Metro + Bus / Auto)\n\n"
                    f"Reply with your choice to complete the route calculation. Adjust maadi!"
                )

    dest_only_match: Optional[re.Match[str]] = re.search(
        r"^(?:i want to go to|how to (?:go|reach)|take me to|going to)\s+([a-z0-9\s\(\)]+?)(?:\?|$)", q
    )
    if dest_only_match and " from " not in q and not is_comparison_query:
        target_dest: str = _normalize_bangalore_area(dest_only_match.group(1).strip())

        # If destination is outside Bangalore, let Gemini LLM handle it
        if not _is_bangalore_area(target_dest):
            return None
        
        # Pick 3 popular starting spots based on destination
        spots: list[str] = ["From Kempegowda (Majestic)", "From Whitefield (Kadugodi)", "From RV Road / Silk Board"]
        if "Electronic City" in target_dest or "Silk Board" in target_dest or "Bommasandra" in target_dest:
            spots = ["From Kempegowda (Majestic)", "From RV Road / Jayanagar", "From Whitefield (Kadugodi)"]
        elif "Whitefield" in target_dest or "ITPL" in target_dest:
            spots = ["From Kempegowda (Majestic)", "From Indiranagar", "From Silk Board / HSR"]
        elif "Cubbon Park" in target_dest or "Mg Road" in target_dest or "Church Street" in target_dest:
            spots = ["From Kempegowda (Majestic)", "From Whitefield (Kadugodi)", "From RV Road / Jayanagar"]

        header_text: str = f"You want to reach *{target_dest}*"

        return (
            f"*Namma Transit Agent — Human-In-The-Loop Clarification*\n\n"
            f"{header_text}. To give you the exact route options, where will you be starting your journey?\n\n"
            f"Select your starting location:\n"
            f"1. {spots[0]}\n"
            f"2. {spots[1]}\n"
            f"3. {spots[2]}\n"
            f"4. Type your custom starting location\n\n"
            f"Reply with your choice to complete the route calculation. Adjust maadi!"
        )

    return None


def _agent_reply(body: str, sender_key: str) -> str:
    """Answer a message through the compiled LangGraph workflow. Pre-routes
    simple, single-intent questions straight to a tool (zero Gemini cost), and
    orchestrates multi-turn tool-calling via LangGraph otherwise."""
    history: list[tuple[str, str]] = MEMORY.history(sender_key)
    direct: str | None = _pre_route(body, chat_history=history)
    if direct is not None:
        logger.info("Pre-routed to tool (no Gemini request used).")
        USAGE.record_direct()
        MEMORY.append(sender_key, "human", body)
        MEMORY.append(sender_key, "ai", direct)
        return direct

    try:
        reply: str = run_transit_workflow(body, session_id=sender_key, tracker=USAGE)
    except Exception as exc:  # pragma: no cover - defensive catch-all
        logger.exception("LangGraph workflow execution failed, attempting fallback: %s", exc)
        try:
            reply = run_agent(body, chat_history=history, tracker=USAGE)
        except Exception as inner_exc:
            if "quota" in str(inner_exc).lower() or "resourceexhausted" in str(inner_exc).lower() or "429" in str(inner_exc):
                return QUOTA_REPLY
            return FALLBACK_REPLY

    MEMORY.append(sender_key, "human", body)
    MEMORY.append(sender_key, "ai", reply)
    return reply


class ChatRequest(BaseModel):
    message: str
    session_id: str


class ResetRequest(BaseModel):
    session_id: str


@app.post("/api/chat")
async def api_chat(payload: ChatRequest) -> dict[str, str]:
    """Primary chat endpoint backing the web UI. Same agent brain as the
    legacy WhatsApp webhook, keyed by browser session instead of number."""
    message: str = payload.message.strip()
    session_key: str = f"web:{payload.session_id.strip()}"

    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    if not payload.session_id.strip():
        raise HTTPException(status_code=400, detail="session_id must not be empty")

    logger.info("Web chat [%s]: %r", payload.session_id[:8], message[:120])

    if not LIMITER.allow(session_key):
        logger.warning("Rate limit hit for %s", session_key)
        return {"reply": RATE_LIMIT_REPLY}

    return {"reply": _agent_reply(message, session_key)}


@app.post("/api/reset")
async def api_reset(payload: ResetRequest) -> dict[str, object]:
    """Clear a web session's conversation memory."""
    MEMORY.reset(f"web:{payload.session_id.strip()}")
    return {"ok": True}


@app.get("/")
async def index() -> FileResponse:
    """Interactive chat UI — the primary user surface."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/frontend", StaticFiles(directory=STATIC_DIR), name="frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    """Twilio WhatsApp sandbox webhook. Consumes Form data, runs the LangChain
    agent on the message body, and returns a TwiML XML response."""
    form = await request.form()
    body: str = str(form.get("Body", "") or "")
    from_number: str = str(form.get("From", "") or "")

    logger.info("Incoming WhatsApp message from %s: %r", from_number, body[:120])

    validator: RequestValidator | None = _twilio_signature_valid()
    if validator is not None:
        signature: Optional[str] = request.headers.get("X-Twilio-Signature")
        url: str = _public_request_url(request)
        params: dict[str, str] = _form_to_params(form)
        if not signature or not validator.validate(url, params, signature):
            logger.warning("Rejected unverified Twilio webhook from %s", from_number)
            return Response(content="Invalid signature", status_code=400)

    if not from_number:
        return Response(content="Missing sender", status_code=400)

    if not LIMITER.allow(from_number):
        logger.warning("Rate limit hit for %s", from_number)
        twiml_rl: MessagingResponse = MessagingResponse()
        twiml_rl.message(RATE_LIMIT_REPLY)
        return Response(content=str(twiml_rl), media_type="application/xml")

    if not body:
        body = "Show available Bengaluru transit options."

    reply: str = _agent_reply(body, from_number)

    twiml: MessagingResponse = MessagingResponse()
    twiml.message(reply)
    return Response(content=str(twiml), media_type="application/xml")


@app.get("/health")
async def health_check() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "namma-transit-agent",
        "version": APP_VERSION,
        "model_pool": MODEL_POOL,
    }


@app.get("/usage")
async def usage_report() -> dict[str, object]:
    """Daily quota observability across the model rotation pool."""
    return USAGE.snapshot()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

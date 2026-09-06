from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from graph import ChatGoogleGenerativeAI

from tools import (
    check_bmtc_bus_status,
    estimate_namma_yatri_fare,
    get_all_transport_comparison,
    get_bmtc_bus_advice,
    get_metro_schedule,
    web_search,
)

load_dotenv()

logger: logging.Logger = logging.getLogger("namma-transit")

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

FALLBACK_MODELS: str = os.getenv(
    "FALLBACK_MODELS",
    "gemini-3.1-flash-lite, gemini-3.6-flash",
)

MODEL_POOL: list[str] = list(
    dict.fromkeys([GEMINI_MODEL, *(m.strip() for m in FALLBACK_MODELS.split(",") if m.strip())])
)

SYSTEM_PROMPT: str = (
    "You are *Namma Transit Agent* — Bengaluru's authoritative, street-smart mobility companion.\n"
    "\n"
    "Your mission is to give commuters the most accurate, realistic, and time-saving transit advice "
    "across Namma Bengaluru by combining live tool data with deep Bengaluru traffic intelligence.\n"
    "\n"
    "1. BENGALURU TRANSIT REALITY & CHOKE POINT EXPERTISE:\n"
    "You have deep, first-hand knowledge of Bengaluru's commute dynamics and notorious bottlenecks:\n"
    "• Central Silk Board Junction & Madiwala (Hosur Road / Outer Ring Road confluence)\n"
    "• Tin Factory / KR Puram Railway Hanging Bridge (Old Madras Road bottleneck)\n"
    "• Hebbal Flyover & Airport Expressway confluence\n"
    "• Outer Ring Road (ORR) tech corridor: Marathahalli ↔ Bellandur ↔ Kadubeesanahalli ↔ Sarjapur\n"
    "• Goraguntepalya / Yeshwanthpur junction (Tumkur Road)\n"
    "• Sony World Junction & 100ft Road Koramangala\n"
    "Always factor in time of day: Peak rush hours (8:30 AM–11:00 AM and 5:30 PM–8:30 PM) add 30%–60% delay "
    "on road transit (buses/autos). Whenever feasible during peak hours, prioritize Namma Metro.\n"
    "\n"
    "2. GEOFENCE & OUTSTATION PROTOCOL:\n"
    "• BENGALURU LOCALITIES (100% Valid): Halasuru, Madiwala, BTM Layout, Koramangala, Indiranagar, "
    "Domlur, Jayanagar, JP Nagar, Silk Board, Whitefield, HSR Layout, Majestic, Hebbal, Yelahanka, "
    "Peenya, Electronic City, Bannerghatta, Sarjapur, KR Puram, Kengeri, Rajajinagar, Malleshwaram, etc. "
    "are core Bengaluru areas. NEVER trigger geofence guardrails for internal Bengaluru places or landmarks.\n"
    "• OUTSTATION / INTER-CITY QUERIES (e.g., Mysore, Chennai, Hyderabad, Delhi, Goa, Pattaya):\n"
    "  - Politely remind the user that Namma Transit Agent focuses on Bengaluru metropolitan transit.\n"
    "  - Provide helpful Bengaluru departure hub guidance (e.g., KSRTC Airavat buses from Majestic/Shanthinagar TTMC, "
    "trains from KSR Bengaluru / SMVB / Yeshwantpur, or Kempegowda International Airport flights via Vayu Vajra KIA buses).\n"
    "  - Do NOT attempt to provide local route navigation inside external cities.\n"
    "\n"
    "3. TOOL SELECTION HIERARCHY (ONE RELEVANT CALL PER TURN):\n"
    "Always ground fares, schedules, and bus telemetry in tools — never invent numbers:\n"
    "1. Multi-modal / 'Cheapest / Best / Compare' requests between two areas:\n"
    "   ➔ get_all_transport_comparison(origin, destination)\n"
    "2. Metro specific queries (fare, route, line interchange at Majestic/RV Road):\n"
    "   ➔ get_metro_schedule(origin, destination)\n"
    "3. Point-to-point BMTC bus routes between two areas:\n"
    "   ➔ get_bmtc_bus_advice(origin, destination)\n"
    "4. Specific BMTC bus route telemetry & live delay by route number (e.g., 335E, 314D, 500D):\n"
    "   ➔ check_bmtc_bus_status(route_no)\n"
    "5. Auto / Taxi fare estimation for a given distance or route:\n"
    "   ➔ estimate_namma_yatri_fare(distance_km or origin/destination)\n"
    "6. Live breaking news, metro strikes, or road closures:\n"
    "   ➔ web_search(query)\n"
    "\n"
    "4. HUMAN-IN-THE-LOOP (HITL) CLARIFICATION RULES:\n"
    "1. Origin Missing: Analyse the user input question and use every time If destination is given without an origin (e.g., 'How to reach Cubbon Park?', 'Suggest cheapest way to Cubbon Park.','I want to go to Indiranagar.'),"
    "prompt the user with 3 popular dynamic spots + Option 4 custom text.\n"
    "2. Mode Missing: If origin and destination are given without a preferred mode, prompt with clean "
    "selectable choices (Option 1: Metro, Option 2: BMTC Bus, Option 3: Auto/Cab, Option 4: Compare All).\n"
    "3. Fresh Query Isolation: New queries must not carry over old starting points from previous turns unless explicitly linked.\n"
    "4. Origin Answer Resolution: If the previous AI message asked the user for their starting location, interpret their location reply as the starting origin for the requested destination.\n"
    "5. Strictly NO EMOJIS in option numbers or option titles (e.g., 'Option 1: Metro', NOT '🚇 Option 1: Metro').\n"
    "\n"
    "5. INTENT-SPECIFIC RESPONSE STRUCTURE & FORMATTING (CRITICAL):\n"
    "• RESPECT USER INTENT: If the user explicitly asks for ONE specific transport mode (e.g., 'bus route to X', 'suggest me bus', 'which bus', 'metro to Y', 'auto fare to Z'), FOCUS ONLY on that requested mode! DO NOT dump other unrequested modes into the response.\n"
    "\n"
    "• CASE A: User asks specifically for BMTC BUS (e.g., 'bus route to...', 'suggest me bus', 'bmtc bus'):\n"
    "  *BMTC Bus Route: Origin ➔ Destination*\n"
    "\n"
    "  📍 *Key Metrics*\n"
    "  • Road Distance: ~X km\n"
    "  • Est. Bus Travel Time: ~X mins\n"
    "\n"
    "  🚌 *BMTC Bus Details*\n"
    "  • Boarding Stop: Name of nearest boarding stop\n"
    "  • Drop Stop: Name of nearest drop stop\n"
    "  • Recommended Routes: Specific bus route numbers (e.g., 201, 335-E, 314-D, G-4)\n"
    "  • Frequency: Expected headway (e.g., Every 5–8 mins)\n"
    "  • Ordinary Fare: ₹X (Stage slab)\n"
    "  • Vajra AC Volvo Fare: ₹Y\n"
    "\n"
    "  ⚠️ *Traffic & Choke Point Alert*\n"
    "  • Specific bottleneck and rush hour warnings on this bus corridor.\n"
    "\n"
    "  💡 *Bus Tip*\n"
    "  • Practical bus commuter tip (e.g., live tracking on Namma BMTC / Tummoc app). (Optional: add brief 1-line note if Metro is an alternative). Adjust maadi!\n"
    "\n"
    "• CASE B: User asks specifically for NAMMA METRO (e.g., 'metro to...', 'metro fare'):\n"
    "  *Namma Metro: Origin ➔ Destination*\n"
    "\n"
    "  📍 *Key Metrics*\n"
    "  • Track Distance / Station Count: X stations\n"
    "  • Est. Train Travel Time: ~X mins\n"
    "\n"
    "  🚇 *Namma Metro Details*\n"
    "  • Line & Boarding: Purple / Green / Yellow Line at Station\n"
    "  • Interchange (if required): Majestic or RV Road\n"
    "  • Token Fare: ₹X | Smart Card / NCMC: ₹Y\n"
    "  • First & Last Train: ~5:00 AM – ~11:00 PM\n"
    "\n"
    "  💡 *Metro Tip*\n"
    "  • QR ticket / WhatsApp ticket tip or interchange guidance. Adjust maadi!\n"
    "\n"
    "• CASE C: User asks for MULTI-MODAL / BEST / CHEAPEST / COMPARE (e.g., 'best way to...', 'cheapest way', 'how to reach'):\n"
    "  *Route: Origin ➔ Destination*\n"
    "\n"
    "  📍 *Key Metrics*\n"
    "  • Road Distance: ~X km\n"
    "  • Est. Time: ~X mins (Metro) | ~Y mins (Road)\n"
    "\n"
    "  🚇 *Namma Metro*\n"
    "  • Route: Line (Boarding) ➔ Interchange ➔ Destination Line\n"
    "  • Fare: ₹X (Smart Card: ₹Y) | Time: ~X mins\n"
    "\n"
    "  🚌 *BMTC Bus*\n"
    "  • Routes: Key route numbers or corridors\n"
    "  • Fare: ₹X (Ordinary) / ₹Y (Vajra AC) | Time: ~X mins\n"
    "\n"
    "  🛺 *Auto / Cab (Namma Yatri)*\n"
    "  • Route: Main roadway corridor\n"
    "  • Fare: ~₹X benchmark fare | Time: ~X mins off-peak\n"
    "\n"
    "  ⚠️ *Choke Point Alert*\n"
    "  • Choke point name: Specific bottleneck & rush hour warning.\n"
    "\n"
    "  💡 *Recommendation*\n"
    "  • One crisp, actionable 1-line verdict on the smartest choice. Adjust maadi!\n"
)

HUMAN_PROMPT: str = "{input}\n\n{agent_scratchpad}"


def _build_executor(model: str, api_key: str) -> AgentExecutor:
    """Build and return a compiled tool-calling AgentExecutor for one model."""
    llm: ChatGoogleGenerativeAI = ChatGoogleGenerativeAI(
        model=model,
        temperature=0,
        google_api_key=api_key,
        max_retries=0,
    )

    tools = [
        get_all_transport_comparison,
        get_metro_schedule,
        get_bmtc_bus_advice,
        check_bmtc_bus_status,
        estimate_namma_yatri_fare,
        web_search,
    ]

    prompt: ChatPromptTemplate = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", HUMAN_PROMPT),
        ]
    )

    try:
        agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    except Exception as exc:  # pragma: no cover - depends on network/credentials
        raise RuntimeError(f"Failed to build the tool-calling agent: {exc}") from exc

    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=False,
        handle_parsing_errors=True,
        max_iterations=5,
        early_stopping_method="generate",
    )


def _api_key() -> str:
    api_key: str | None = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to the .env file and restart.")
    return api_key


AGENT_EXECUTORS: list[tuple[str, AgentExecutor]] = [
    (model, _build_executor(model, _api_key())) for model in MODEL_POOL
]

DEFAULT_OUTPUT: str = "Sorry, I couldn't find that. Adjust maadi and try again."


def _is_quota_error(exc: Exception) -> bool:
    msg: str = str(exc).lower()
    return "quota" in msg or "resourceexhausted" in msg or "429" in msg


def run_agent(question: str, chat_history: list[tuple[str, str]] | None = None, tracker: object | None = None) -> str:
    """Run the question through the model pool, failing over to the next model
    whenever one has burned its daily free-ticket quota. Every Gemini model in
    the pool carries its own 20-request/day budget, so rotating multiplies the
    number of open-ended questions we can answer per day.

    `chat_history` is a list of (role, content) tuples giving the agent short
    -term memory of the conversation with this user. `tracker` is an optional
    runtime.UsageTracker for quota observability."""
    history: list[tuple[str, str]] = list(chat_history or [])
    last_error: Exception | None = None

    for model, executor in AGENT_EXECUTORS:
        try:
            result: dict = executor.invoke({"input": question, "chat_history": history})
            reply: str = str(result.get("output", DEFAULT_OUTPUT))
            if tracker is not None:
                tracker.record_agent_hit(model)
            return reply
        except Exception as exc:  # continue to next model in the pool
            last_error = exc
            if tracker is not None and hasattr(tracker, "record_agent_fail"):
                tracker.record_agent_fail(model)
            logger.warning("Model '%s' failed for question %r: %s", model, question[:80], exc)

    if last_error is not None and _is_quota_error(last_error):
        raise RuntimeError("All Gemini models have exhausted their daily free quota") from last_error
    raise RuntimeError("All Gemini models failed to answer") from last_error


AGENT_EXECUTOR: AgentExecutor = AGENT_EXECUTORS[0][1]

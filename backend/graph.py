from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Optional, TypedDict

from dotenv import load_dotenv
import json
import uuid
import httpx
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    FunctionMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool


class ChatGoogleGenerativeAI(BaseChatModel):  # type: ignore
    """Pure-Python REST HTTP implementation of Gemini Chat Model with tool calling.
    Eliminates all binary gRPC dependencies / DLL lock issues on Windows and Linux."""
    model: str = "gemini-2.5-flash"
    temperature: float = 0.0
    google_api_key: str = ""
    timeout: float = 25.0
    bound_tools: list[dict[str, Any]] = []
    model_kwargs: dict[str, Any] = {}

    @property
    def _llm_type(self) -> str:
        return "gemini-rest-chat-model"

    def bind_tools(self, tools: list[BaseTool | dict[str, Any]], **kwargs: Any) -> ChatGoogleGenerativeAI:
        formatted_tools = []
        for tool in tools:
            if hasattr(tool, "name") and hasattr(tool, "description"):
                schema = getattr(tool, "args_schema", None)
                params: dict[str, Any] = {"type": "OBJECT", "properties": {}}
                required = []
                if schema and hasattr(schema, "model_json_schema"):
                    json_schema = schema.model_json_schema()
                    props = json_schema.get("properties", {})
                    required = json_schema.get("required", [])
                    gemini_props = {}
                    for k, v in props.items():
                        gemini_props[k] = {
                            "type": "STRING" if v.get("type") == "string" else "NUMBER" if v.get("type") in ("number", "integer") else "OBJECT",
                            "description": v.get("description", ""),
                        }
                    params = {"type": "OBJECT", "properties": gemini_props}
                    if required:
                        params["required"] = required
                elif hasattr(tool, "args"):
                    gemini_props = {}
                    for k, v in tool.args.items():
                        gemini_props[k] = {"type": "STRING", "description": v.get("description", "")}
                    params = {"type": "OBJECT", "properties": gemini_props}

                formatted_tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": params,
                })
            elif isinstance(tool, dict):
                formatted_tools.append(tool)

        new_instance = self.model_copy()
        new_instance.bound_tools = formatted_tools
        return new_instance

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        api_key = self.google_api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is missing.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={api_key}"

        system_instruction = None
        contents = []

        for msg in messages:
            if isinstance(msg, SystemMessage):
                system_instruction = {"parts": [{"text": str(msg.content)}]}
            elif isinstance(msg, HumanMessage):
                contents.append({"role": "user", "parts": [{"text": str(msg.content)}]})
            elif isinstance(msg, AIMessage):
                parts: list[dict[str, Any]] = []
                thought_sig = msg.additional_kwargs.get("thoughtSignature") if hasattr(msg, "additional_kwargs") else None
                if msg.content:
                    p = {"text": str(msg.content)}
                    if thought_sig:
                        p["thoughtSignature"] = thought_sig
                    parts.append(p)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        p = {
                            "functionCall": {
                                "name": tc["name"],
                                "args": tc["args"],
                            }
                        }
                        if thought_sig:
                            p["thoughtSignature"] = thought_sig
                        parts.append(p)
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif isinstance(msg, (ToolMessage, FunctionMessage)):
                tool_name = getattr(msg, "name", "tool") or "tool"
                content_text = str(msg.content)
                contents.append({
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": tool_name,
                                "response": {"output": content_text},
                            }
                        }
                    ],
                })

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
            },
        }
        if system_instruction:
            payload["system_instruction"] = system_instruction
        if self.bound_tools:
            payload["tools"] = [{"function_declarations": self.bound_tools}]

        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = httpx.post(url, json=payload, timeout=self.timeout)
                if resp.status_code != 200:
                    logger.warning("Gemini REST API returned %d on model %s: %s", resp.status_code, self.model, resp.text)
                    raise RuntimeError(f"Gemini API error {resp.status_code}")

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError(f"No candidates generated on model {self.model}")

                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])
                tool_calls = []
                text_parts = []
                thought_sig = None

                for p in parts:
                    if "thoughtSignature" in p:
                        thought_sig = p["thoughtSignature"]
                    if "functionCall" in p:
                        fc = p["functionCall"]
                        tool_calls.append({
                            "name": fc["name"],
                            "args": fc.get("args", {}),
                            "id": fc.get("id") or str(uuid.uuid4()),
                        })
                    elif "text" in p:
                        text_parts.append(p["text"])

                out_text = "".join(text_parts).strip()
                additional_kwargs: dict[str, Any] = {}
                if thought_sig:
                    additional_kwargs["thoughtSignature"] = thought_sig
                ai_msg = AIMessage(content=out_text, tool_calls=tool_calls, additional_kwargs=additional_kwargs)
                return ChatResult(generations=[ChatGeneration(message=ai_msg)])

            except Exception as e:
                last_err = e
                logger.warning("Gemini REST call attempt %d on model %s failed: %s", attempt + 1, self.model, e)

        raise RuntimeError(f"Model {self.model} failed: {last_err}")


from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from tools import (
    check_bmtc_bus_status,
    estimate_namma_yatri_fare,
    get_all_transport_comparison,
    get_bmtc_bus_advice,
    get_metro_schedule,
    web_search,
)

load_dotenv()

logger: logging.Logger = logging.getLogger("namma-transit.graph")

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODELS: str = os.getenv(
    "FALLBACK_MODELS",
    "gemini-3.1-flash-lite, gemini-3.6-flash",
)

MODEL_POOL: list[str] = list(
    dict.fromkeys([GEMINI_MODEL, *(m.strip() for m in FALLBACK_MODELS.split(",") if m.strip())])
)

TOOLS = [
    get_all_transport_comparison,
    get_metro_schedule,
    get_bmtc_bus_advice,
    check_bmtc_bus_status,
    estimate_namma_yatri_fare,
    web_search,
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}

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
    "1. Origin Missing: If destination is given without an origin (e.g., 'How to reach Cubbon Park?'), "
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


class TransitState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    direct_response: Optional[str]


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to .env and restart.")
    return key


def _get_last_user_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
        if isinstance(m, dict) and m.get("role") in ("user", "human"):
            return str(m.get("content", ""))
    return ""


def geofence_node(state: TransitState) -> dict[str, Any]:
    """Check if the user query contains out-of-Bangalore locations."""
    from main import OUTSIDE_BANGALORE_REPLY, _detect_outside_bangalore

    text = _get_last_user_text(state["messages"])
    if text and _detect_outside_bangalore(text):
        return {"direct_response": OUTSIDE_BANGALORE_REPLY}
    return {}


def direct_router_node(state: TransitState) -> dict[str, Any]:
    """Execute zero-cost deterministic intent routing & HITL options."""
    if state.get("direct_response"):
        return {}

    from main import _pre_route

    text = _get_last_user_text(state["messages"])
    if text:
        res = _pre_route(text)
        if res:
            return {"direct_response": res}
    return {}


def agent_node(state: TransitState) -> dict[str, Any]:
    """Call Google Gemini tool-calling model with model pool rotation."""
    if state.get("direct_response"):
        return {}

    api_key = _get_api_key()
    messages = list(state["messages"])
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

    last_exc = None
    for model_name in MODEL_POOL:
        try:
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=0,
                google_api_key=api_key,
                max_retries=0,
                model_kwargs={"thinking_config": {"thinking_budget": 0}} if "3" in model_name else {},
            )
            llm_with_tools = llm.bind_tools(TOOLS)
            response = llm_with_tools.invoke(messages)
            return {"messages": [response]}
        except Exception as exc:
            logger.warning("Model %s failed in LangGraph agent_node: %s", model_name, exc)
            last_exc = exc

    # If all models in the pool failed (e.g. rate limits), smart local tool fallback:
    import re
    last_user = _get_last_user_text(state["messages"]).lower()
    bus_m = re.search(r"\b(kias-?\d+[a-z]?|[a-z]*\d{1,4}[a-z]?)\b", last_user)
    if bus_m and any(w in last_user for w in ("bus", "bmtc", "route", "status", "track", "delayed")):
        bus_out = check_bmtc_bus_status.invoke({"route_no": bus_m.group(1).upper()})
        return {"messages": [AIMessage(content=bus_out)]}

    from_to_m = re.search(r"from\s+(.+?)\s+to\s+(.+)$", last_user)
    if from_to_m:
        from tools import get_all_transport_comparison
        comp_out = get_all_transport_comparison(from_to_m.group(1).strip(), from_to_m.group(2).strip())
        return {"messages": [AIMessage(content=comp_out)]}

    # Fallback message on total quota exhaustion
    fallback_reply = AIMessage(
        content="Aiyyo, our AI transit servers are currently experiencing heavy traffic. Please ask for a specific metro fare or bus route directly! Adjust maadi."
    )
    return {"messages": [fallback_reply]}


def tools_node(state: TransitState) -> dict[str, Any]:
    """Execute tool calls generated by the agent."""
    messages = state["messages"]
    last_msg = messages[-1]
    tool_messages: list[ToolMessage] = []

    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        for tool_call in last_msg.tool_calls:
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            call_id = tool_call.get("id", "call_1")

            tool_func = TOOLS_BY_NAME.get(tool_name)
            if tool_func:
                try:
                    tool_output = str(tool_func.invoke(tool_args))
                except Exception as err:
                    tool_output = f"Error executing {tool_name}: {err}"
            else:
                tool_output = f"Tool {tool_name} not found."

            tool_messages.append(ToolMessage(content=tool_output, tool_call_id=call_id, name=tool_name))

    return {"messages": tool_messages}


def router_condition(state: TransitState) -> str:
    """Determine whether to exit immediately or invoke the Gemini agent."""
    if state.get("direct_response"):
        return "end"
    return "agent"


def tools_condition(state: TransitState) -> str:
    """Determine whether the agent requested tool execution."""
    if state.get("direct_response"):
        return "end"
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "end"


def build_transit_graph() -> StateGraph:
    """Construct and compile the Namma Transit LangGraph workflow."""
    workflow = StateGraph(TransitState)

    workflow.add_node("geofence_node", geofence_node)
    workflow.add_node("direct_router_node", direct_router_node)
    workflow.add_node("agent_node", agent_node)
    workflow.add_node("tools_node", tools_node)

    workflow.add_edge(START, "geofence_node")
    workflow.add_edge("geofence_node", "direct_router_node")

    workflow.add_conditional_edges(
        "direct_router_node",
        router_condition,
        {
            "end": END,
            "agent": "agent_node",
        },
    )

    workflow.add_conditional_edges(
        "agent_node",
        tools_condition,
        {
            "tools": "tools_node",
            "end": END,
        },
    )

    workflow.add_edge("tools_node", "agent_node")

    return workflow


# Compile state graph with memory checkpointer
memory_checkpointer = MemorySaver()
transit_graph = build_transit_graph().compile(checkpointer=memory_checkpointer)


def run_transit_workflow(
    question: str,
    session_id: str = "default",
    tracker: object | None = None,
) -> str:
    """Entry point for executing the compiled LangGraph workflow."""
    config = {"configurable": {"thread_id": session_id}}
    initial_input: dict[str, Any] = {
        "messages": [HumanMessage(content=question)],
        "direct_response": None,
    }

    result = transit_graph.invoke(initial_input, config=config)

    # 1. If direct zero-cost response or geofence response was computed:
    if result.get("direct_response"):
        if tracker is not None and hasattr(tracker, "record_direct_hit"):
            tracker.record_direct_hit()
        return str(result["direct_response"])

    # 2. Otherwise extract final AI Message:
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            if tracker is not None and hasattr(tracker, "record_agent_hit"):
                tracker.record_agent_hit(GEMINI_MODEL)
            return str(msg.content)

    return "Aiyyo! Could not process the route. Adjust maadi and try again."

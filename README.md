# Namma Transit Agent 🚇🚌🛺

Bengaluru's premier AI mobility assistant. Ask anything about **Namma Metro (Purple, Green & Yellow lines)**, **BMTC buses**, or **Namma Yatri auto fares** — get live route telemetry, interchange guidance, delay alerts, interactive Human-In-The-Loop (HITL) options, and multi-modal commute comparisons through an ambient SaaS frosted glass web interface powered by **LangGraph** and **Google Gemini**.

```
Browser ──▶ FastAPI  /            → Ambient SaaS Web Chat UI (frontend/)
         ▶ FastAPI  POST /api/chat → Zero-Cost Pre-Router → LangGraph Agent + Gemini REST API
                                             │
Twilio ────▶ ngrok ──▶ /whatsapp      ← WhatsApp Webhook Integration
```

---

## ✨ Key Features

### 1. 🎨 Modern SaaS Frosted Glass UI & Editorial Typography
- **Ambient Fullscreen Video**: Subtle looping background video with an ultra-smooth backdrop blur overlay (`backdrop-filter: blur(28px)`).
- **Editorial Typography**: **`Instrument Serif`** for hero display headings paired with **`Inter`** for ultra-crisp conversational readability and **`JetBrains Mono`** for metrics.
- **Design Tokens**: Charcoal text (`#1e293b`), electric indigo accents (`#6366f1`), dashboard elevation shadows (`--shadow-dashboard`), and rounded pill buttons (`border-radius: 9999px`).
- **Interactive Disappearing Option Pills**: Dynamic choices submit on click and cleanly animate away to keep the chat tidy.

### 2. ⚡ Pure-Python REST Gemini Architecture (Zero-DLL Transport)
- **Direct HTTP/REST API**: Uses `httpx` to communicate directly with Google's Gemini v1beta endpoint, eliminating all binary C++ gRPC dependencies and Windows DLL lock issues.
- **`thoughtSignature` Integrity**: Preserves cryptographic multi-turn thought signatures across tool executions.
- **Active Model Pool**: Configured with high-quota, low-latency models:
  - `gemini-3.5-flash-lite` (Primary)
  - `gemini-3.1-flash-lite` (Secondary Fallback)
  - `gemini-3.6-flash` (Tertiary Fallback)
- **Zero-Downtime Smart Fallback**: If external AI models encounter quota limits, the agent automatically executes our local transit engine directly, so users never see error screens.

### 3. 🚍 Authoritative BMTC Route Directory (`BMTC_ROUTES_DB`)
- Pre-loaded with verified stop-to-stop corridors, origins, destinations, and realistic distances:
  - **`314` Series (`314`, `314-D`, `314-B`, `314-E`)**: Shivajinagar / Majestic ↔ Indiranagar ↔ CV Raman Nagar / Nagavarapalya / GM Palya.
  - **`335` Series (`335-E`, `335-A`)**: Majestic ↔ Old Airport Road ↔ Marathahalli ↔ Kadugodi / ITPL.
  - **`500` Series (`500-D`, `500-C`, `500-CA`)**: Silk Board ↔ Bellandur ↔ Marathahalli ↔ Hebbal (ORR).
  - **`201` Series (`201`, `201-G`)**: Banashankari ↔ Koramangala ↔ Domlur ↔ CV Raman Nagar.
  - **`356` & `360` Series**: Majestic ↔ Silk Board ↔ Electronic City ↔ Attibele.
  - **`365` Series**: Majestic ↔ Dairy Circle ↔ Bannerghatta National Park.
  - **Airport Express**: `KIA-8`, `KIA-9`, `KIA-14`, `KIA-4` (Vayu Vajra).
  - **Big Trunk**: `G-1` through `G-4`.

### 4. 🚇 Multi-Line Namma Metro System (Purple, Green & Yellow Lines)
- **Purple Line**: Challaghatta ↔ Whitefield (Kadugodi) [37 stations, 43.49 km]
- **Green Line**: Madavara ↔ Silk Institute [31 stations, 33.46 km]
- **Yellow Line**: RV Road ↔ Bommasandra (via Silk Board & Electronic City) [14 stations, 19.1 km]
- **Smart Interchange Routing**: Cross-line interchange math at **Majestic (Nadaprabhu Kempegowda)** and **RV Road** (Green ↔ Yellow).
- **Official BMRCL Fares**: Exact stage fares (₹10–₹90) + Smart Card / NCMC discounts (5%–10%).

### 5. 🤝 Human-In-The-Loop (HITL) Interactive Clarifications
- **Origin Clarification**: When only a destination is requested (e.g. *"I want to go to Indiranagar"*), prompts with dynamic popular origins + custom text input.
- **Mode Selection**: Prompts user for preferred travel mode (Metro, Bus, Auto, or Multimodal Mix) with 1-click selectable pills.

### 6. 🛡️ Intelligent Bengaluru Geofencing
- Dedicated exclusively to the Bengaluru urban mobility network.
- Outstation requests (e.g. Chennai, Delhi, Pattaya) are handled conversationally with inter-city transit recommendations (KSRTC Airavat / Indian Railways / Kempegowda International Airport flights).

---

## 📁 Repository Structure

```
namma-transit-agent/
├── backend/
│   ├── main.py            # FastAPI app, endpoints, pre-router & session runtime
│   ├── graph.py           # LangGraph ReAct workflow, RestGeminiChatModel & model pool
│   ├── agent.py           # AgentExecutor setup & tool bindings
│   ├── tools.py           # BMRCL Metro fares, BMTC_ROUTES_DB, OSRM & TomTom traffic
│   ├── runtime.py         # Sliding window conversation memory & rate limiter
│   ├── requirements.txt   # Backend Python dependencies
│   └── tests/             # Automated Pytest suite (66 tests)
│       ├── test_api.py
│       ├── test_metro.py
│       ├── test_routing.py
│       └── test_runtime.py
├── frontend/
│   ├── index.html         # SaaS layout, ambient video & typography imports
│   ├── style.css          # Frosted glass styling, design tokens & animations
│   ├── app.js             # Client-side messaging, interactive pills & session handling
│   └── assets/            # Ambient background video asset
├── render.yaml            # Render 1-click Web Service Blueprint
├── vercel.json            # Vercel deployment configuration
├── requirements.txt       # Root deployment requirements
├── run.ps1                # One-click local launch script
└── README.md
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+ installed
- Google Gemini API Key (from [Google AI Studio](https://aistudio.google.com/))

### 2. Setup & Configuration
Create or update your `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
FALLBACK_MODELS=gemini-3.1-flash-lite, gemini-3.6-flash
API_TIMEOUT_SECONDS=2.0
DEV_MODE=true
```

### 3. Run the Application

#### Option A: One-Click PowerShell Script (Recommended)
```powershell
.\run.ps1
```

#### Option B: Direct Python / Uvicorn
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser and navigate to:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 🧪 Running Automated Tests

Run the full automated pytest suite (66 passing unit & integration tests):

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

---

## 🌐 API Surface

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Ambient SaaS Web Chat Interface |
| `/static/*` | GET | Frontend static assets (`frontend/`) |
| `/api/chat` | POST | JSON chat endpoint: `{"message": "...", "session_id": "..."}` |
| `/api/reset` | POST | Clear conversation memory for a session |
| `/whatsapp` | POST | Twilio webhook for WhatsApp integration |
| `/health` | GET | Service health, version & active model pool |
| `/usage` | GET | Real-time usage analytics & routing breakdown |

---

## ☁️ Cloud Deployment

### Deploy to Render
1. Push this repository to GitHub.
2. Link your GitHub repository in [Render](https://render.com/).
3. Render will detect `render.yaml` automatically.
4. Add your `GEMINI_API_KEY` under Environment Variables.

### Deploy to Vercel
1. Run `vercel` from the root directory or import into Vercel Dashboard.
2. Vercel uses `vercel.json` to route requests to the FastAPI ASGI application.

---

## 💡 Example Queries to Try

| Question | Resolution Engine | Output Details |
|---|---|---|
| `metro fare from Majestic to Electronic City` | Purple ↔ Yellow Interchange Tool | ₹60 Token / ₹57 Smart Card (45 mins) |
| `is bus 335e delayed?` | Gemini LLM + BMTC Telemetry | +18 mins delay alert at Silk Board |
| `Track bus 314-D` | Gemini LLM + `BMTC_ROUTES_DB` | Shivajinagar ➔ Nagavarapalya (12 km, on-time) |
| `Cheapest way from halasuru to jayanagar` | Gemini LLM Multi-Modal Comparison | Metro (₹60) vs Bus (₹25) vs Auto (₹176) |
| `I want to go to Church Street` | HITL Origin Clarification | 4 clickable starting location pills |
| `How do I travel to Chennai?` | Gemini LLM Inter-City Guidance | KSRTC Airavat & Shatabdi Express recommendations |
| `auto fare for 7.5 km` | Namma Yatri Metric Tool | ₹113 benchmark fare (Karnataka Govt slab) |

---

**Namma Transit Agent** — *Smart mobility for Namma Bengaluru. Adjust maadi!* 🚇🚌🛺
#   n a m m a - t r a n s i t - a g e n t  
 #   n a m m a - t r a n s i t - a g e n t  
 
# Compass

Paste a GitHub URL. Get an instant AI-generated brief of the codebase — text, audio, and PDF — plus a live chat interface to ask questions about the code.

---

## What it does

1. **Clones** any public GitHub repo into a temp directory
2. **Analyses** it with a 4-pass Gemini pipeline that builds a knowledge graph (file purposes, call graph, features, architecture)
3. **Generates** a structured text brief you can read in 90 seconds
4. **Streams** an audio brief via bidirectional voice using Google ADK + Gemini Live
5. **Exports** a PDF brief via ReportLab
6. **Chats** — after analysis you can ask anything about the codebase in a persistent multi-turn chat grounded in the knowledge graph

Sessions are saved in localStorage and resumable from the sidebar.

---

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + Uvicorn |
| AI — analysis & chat | `gemini-3-flash-preview` via `google-genai` SDK |
| AI — audio | `gemini-2.5-flash-native-audio-preview-12-2025` via Google ADK |
| PDF | ReportLab |
| Frontend | Vanilla JS + Web Audio API |
| Repo cloning | GitPython |

---

## Project structure

```
compass/
├── main.py                  # FastAPI app, router registration, static mount
│
├── ingestion/
│   ├── cloner.py            # Clones GitHub repo to a temp dir
│   ├── reader.py            # Reads repo files into a flat string
│   └── extractor.py        # 4-pass Gemini analysis → knowledge graph JSON
│
├── knowledge/
│   ├── graph.py             # Save / load / format knowledge graph per session
│   └── conversations.py     # In-memory multi-turn chat history store
│
├── briefing/
│   ├── text.py              # Generates markdown text brief from knowledge graph
│   ├── audio.py             # ADK bidirectional audio session handler
│   ├── agent.py             # ADK Agent + Runner singleton
│   ├── chat.py              # Streams chat responses grounded in knowledge graph
│   └── pdf.py               # ReportLab PDF brief generator
│
├── routes/
│   ├── ingest.py            # POST /ingest — SSE streaming ingestion progress
│   ├── text.py              # GET  /brief/text/{session_id}
│   ├── pdf.py               # GET  /brief/pdf/{session_id}
│   ├── chat.py              # POST /chat/{session_id} — SSE streaming chat
│   └── websocket.py         # WS   /ws/{session_id} — audio bidi stream
│
└── frontend/
    ├── index.html           # Single-page app (sidebar, chat, input bar)
    └── js/
        ├── main.js          # App logic, session store, ingestion, chat
        └── audio-manager.js # Web Audio playback + mic capture (ADK audio)
```

---

## Architecture

### System overview

```mermaid
graph TD
    Browser["Browser (vanilla JS)"]
    FastAPI["FastAPI (main.py)"]
    Ingestion["ingestion/\nclone → read → analyse"]
    Knowledge["knowledge/\nin-memory graph store"]
    Briefing["briefing/\ntext · audio · pdf · chat"]
    Gemini["Gemini API"]
    ADK["Google ADK\n(Gemini Live)"]

    Browser -->|"POST /ingest (SSE)"| FastAPI
    Browser -->|"GET /brief/text"| FastAPI
    Browser -->|"GET /brief/pdf"| FastAPI
    Browser -->|"POST /chat (SSE)"| FastAPI
    Browser -->|"WS /ws (bidi audio)"| FastAPI

    FastAPI --> Ingestion
    FastAPI --> Briefing
    Ingestion --> Knowledge
    Briefing --> Knowledge

    Ingestion -->|"generate_content_stream"| Gemini
    Briefing -->|"generate_content\ngenerate_content_stream"| Gemini
    Briefing -->|"StreamingMode.BIDI"| ADK
```

### Data flow

```mermaid
flowchart TD
    URL["GitHub URL"]
    Clone["clone_repo()\nshallow depth=1 → temp dir"]
    Read["read_repo()\nfilter + truncate_to_limit() 900k tokens"]
    Passes["run_all_passes()\n4× generate_content_stream"]
    Graph["knowledge graph\n{per_file, connections,\nfeatures, architecture}"]

    TextBrief["GET /brief/text\ngemini-3-flash-preview → markdown"]
    PDF["GET /brief/pdf\nReportLab → PDF bytes"]
    Chat["POST /chat\ngemini-3-flash-preview SSE"]
    Audio["WS /ws\nADK + Gemini Live bidi audio"]

    URL --> Clone --> Read --> Passes --> Graph
    Graph --> TextBrief
    Graph --> PDF
    Graph --> Chat
    Graph --> Audio
```

### Ingestion sequence

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as ingest route
    participant E as extractor.py
    participant G as Gemini API

    B->>R: POST /ingest {github_url}
    R-->>B: SSE: start (session_id)
    R->>R: clone_repo() + read_repo()
    R-->>B: SSE: cloning / reading

    R->>E: asyncio.create_task(run_all_passes)
    Note over R,E: extraction runs concurrently<br/>with SSE drain loop

    E->>G: Pass 1 — per_file metadata
    G-->>E: streaming JSON
    E-->>R: progress_queue ticks
    R-->>B: SSE: pass_1 (file paths live)

    E->>G: create context cache (TTL 600s)

    E->>G: Pass 2 — connections / call graph
    R-->>B: SSE: pass_2
    E->>G: Pass 3 — features
    R-->>B: SSE: pass_3 (feature names live)
    E->>G: Pass 4 — architecture synthesis
    R-->>B: SSE: pass_4

    R->>R: knowledge.graph.save() + cleanup
    R-->>B: SSE: complete
```

### Audio pipeline

```mermaid
sequenceDiagram
    participant B as Browser
    participant WS as websocket route
    participant ADK as Google ADK
    participant G as Gemini Live

    B->>WS: WS connect /ws/{session_id}
    WS->>ADK: create_session()
    WS->>ADK: queue.send_content(knowledge_graph)

    par upstream_task
        loop mic frames
            B->>WS: {type:audio, data:base64_pcm_16k}
            WS->>ADK: queue.send_realtime(Blob)
        end
        B->>WS: {type:stop}
        WS->>ADK: queue.close()
    and downstream_task
        ADK->>G: runner.run_live(BIDI)
        G-->>WS: audio PCM 24kHz parts
        WS-->>B: {event:audio, data:base64}
        G-->>WS: transcript text
        WS-->>B: {event:transcript, text}
        G-->>WS: turn_complete
        WS-->>B: {event:ready}
    end
```

For full architectural detail see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Setup

### 1. Clone and install

```bash
git clone <this-repo>
cd compass
pip install -r requirements.txt
```

### 2. Environment

Create a `.env` file in the `compass/` directory:

```
GOOGLE_API_KEY=your_gemini_api_key_here
```

### 3. Run

```bash
cd compass
uvicorn main:app --reload
```

Open `http://localhost:8000`.

---

## How the 4-pass analysis works

Each pass sends the full repository content to Gemini and streams results live:

| Pass | What it builds |
|---|---|
| Pass 1 | Per-file metadata — purpose, functions, imports, key logic |
| Pass 2 | Call graph — which files call which, data flow between them |
| Pass 3 | Feature map — user-facing features and their entry points |
| Pass 4 | Architecture summary — patterns, frameworks, design decisions |

The four passes are merged into a single knowledge graph JSON saved per session, which grounds all subsequent text, audio, PDF, and chat outputs.

---

## Audio brief

The audio brief uses Google ADK (`google-adk`) with `StreamingMode.BIDI` for full-duplex conversation. The frontend captures mic audio at 16 kHz via `ScriptProcessorNode` and plays back 24 kHz audio using Web Audio API with a 180 ms jitter buffer and gapless pre-scheduled playback.

---

## Environment variables

| Variable | Description |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key (required) |

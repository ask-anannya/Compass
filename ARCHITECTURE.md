# Compass — Architecture

## Overview

Compass is a single-server web application. FastAPI serves both the REST/WebSocket API and the static frontend from the same process. There is no database — all state is held in two in-memory Python dicts (knowledge graphs and conversation histories) and in the browser's `localStorage`.

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

---

## Request lifecycle

### 1. Ingestion (`POST /ingest`)

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as ingest route
    participant E as extractor.py
    participant G as Gemini API

    B->>R: POST /ingest {github_url}
    R-->>B: SSE: start (session_id)
    R->>R: clone_repo() shallow git clone
    R-->>B: SSE: cloning
    R->>R: read_repo() + truncate_to_limit()
    R-->>B: SSE: reading (N files, ~M tokens)

    R->>E: asyncio.create_task(run_all_passes)
    Note over R,E: extraction runs concurrently<br/>with SSE drain loop

    E->>G: Pass 1 — generate_content_stream
    G-->>E: streaming JSON chunks
    E-->>R: progress_queue → pass_1 ticks
    R-->>B: SSE: pass_1 (file paths as seen)

    E->>G: create context cache (TTL 600s)

    E->>G: Pass 2 — generate_content_stream
    G-->>E: streaming JSON chunks
    E-->>R: progress_queue → pass_2 ticks
    R-->>B: SSE: pass_2 (file paths as seen)

    E->>G: Pass 3 — generate_content_stream
    G-->>E: streaming JSON chunks
    E-->>R: progress_queue → pass_3 ticks
    R-->>B: SSE: pass_3 (feature names as seen)

    E->>G: Pass 4 — generate_content_stream
    G-->>E: streaming JSON chunks
    E-->>R: progress_queue → pass_4 ticks
    R-->>B: SSE: pass_4

    E->>G: delete context cache
    R->>R: knowledge.graph.save(session_id, graph)
    R->>R: cleanup temp dir
    R-->>B: SSE: complete (session_id)
```

The extraction task and the SSE drain loop run concurrently. `extractor.run_all_passes()` puts progress events into an `asyncio.Queue`; the route generator drains that queue with a 1-second timeout that acts as a heartbeat to keep the SSE connection alive through proxies.

### 2. 4-pass analysis pipeline (`ingestion/extractor.py`)

Each pass streams Gemini output with `generate_content_stream` and regex-scans the accumulating JSON for entity names (file paths, feature names) to emit progress ticks in real time before the full response is parsed.

```mermaid
flowchart LR
    RC["repo_content\n(packed string)"]

    RC --> P1["Pass 1\nper_file[]"]
    RC --> Cache{"Context cache\nTTL 600s\n(best-effort)"}
    Cache -->|hit| P2["Pass 2\nconnections[]"]
    Cache -->|hit| P3["Pass 3\nfeatures[]"]
    Cache -->|hit| P4["Pass 4\narchitecture{}"]
    RC -->|"cache miss\n(fallback)"| P2
    RC -->|"cache miss\n(fallback)"| P3
    RC -->|"cache miss\n(fallback)"| P4

    P1 & P2 & P3 & P4 --> KG["knowledge graph\n{per_file, connections,\nfeatures, architecture}"]
```

| Pass | Prompt asks for | Progress ticks on |
|---|---|---|
| 1 | `per_file` array — purpose, functions, imports, key logic | Each `"path"` field seen |
| 2 | `connections` array — calls, called_by, data_flow | Each `"path"` field seen |
| 3 | `features` array — feature name, files, entry point, description | Each `"feature"` field seen |
| 4 | `architecture` object — pattern, language, framework, conventions, rules | pattern/language/framework fields |

The four pass results are merged into a single knowledge graph dict:

```python
{
  'per_file':     [...],   # list of file objects
  'connections':  [...],   # list of connection objects
  'features':     [...],   # list of feature objects
  'architecture': {...}    # single object
}
```

### 3. Knowledge graph store (`knowledge/graph.py`)

A module-level `dict[str, dict]` keyed by `session_id`. No persistence — graphs are lost on server restart. `to_context_string()` flattens the graph to a compact ~8 000-token string used as context injection for all downstream outputs (text brief, chat, audio brief).

> **Production note:** replace `_store` with Redis + 2-hour TTL.

### 4. Text brief (`GET /brief/text/{session_id}`)

Loads the graph, calls `to_context_string()`, and sends a single non-streaming `generate_content` call to `gemini-3-flash-preview` with a fixed formatting prompt. Returns `{ brief: string, graph: dict }` — the `graph` field is used by the frontend after ingestion.

### 5. Chat (`POST /chat/{session_id}`)

SSE-streamed multi-turn chat grounded in the knowledge graph.

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as chat route
    participant C as briefing/chat.py
    participant H as conversations.py
    participant G as Gemini API

    B->>R: POST /chat/{session_id} {message}
    R->>H: append_turn(user, message)
    R->>C: stream_chat_response(session_id, message)
    C->>C: to_context_string(graph)
    C->>H: get_history(session_id)
    Note over C: Build contents[]:<br/>seed context turn<br/>+ prior history (≤40)<br/>+ new message
    C->>G: generate_content_stream(contents)
    G-->>C: text chunks
    C-->>R: yield chunk
    R-->>B: SSE: {event:chunk, text:...}
    R->>H: append_turn(model, full_response)
    R-->>B: SSE: {event:done}
```

Chat history is also persisted in `localStorage` on the client and replayed on session load, so the UI survives server restarts.

### 6. Audio brief (`WebSocket /ws/{session_id}`)

Uses Google ADK (`google-adk`) for bidirectional audio via Gemini Live.

```mermaid
sequenceDiagram
    participant B as Browser
    participant WS as websocket route
    participant ADK as Google ADK
    participant G as Gemini Live

    B->>WS: WS connect /ws/{session_id}
    WS->>ADK: create_session(adk_session_id)
    WS->>ADK: queue.send_content(knowledge_graph)
    Note over WS: asyncio.create_task × 2

    par upstream_task
        loop mic frames
            B->>WS: {type:audio, data:base64_pcm_16k}
            WS->>ADK: queue.send_realtime(Blob pcm/16000)
        end
        B->>WS: {type:stop}
        WS->>ADK: queue.close()
    and downstream_task
        ADK->>G: runner.run_live(BIDI)
        loop audio parts
            G-->>ADK: audio PCM 24kHz
            ADK-->>WS: event.content.parts
            WS-->>B: {event:audio, data:base64}
        end
        G-->>ADK: transcript text
        ADK-->>WS: event.content.parts
        WS-->>B: {event:transcript, text}
        G-->>ADK: turn_complete
        WS-->>B: {event:ready}
    end

    Note over WS: asyncio.wait(FIRST_COMPLETED)<br/>cancels the other task
```

- **Agent singleton** (`briefing/agent.py`): `Agent` + `Runner` + `InMemorySessionService` — initialised once at import time.
- **Model**: `gemini-2.5-flash-native-audio-preview-12-2025`
- **Streaming mode**: `StreamingMode.BIDI` — full-duplex
- **Voice**: Charon (prebuilt)

### 7. PDF (`GET /brief/pdf/{session_id}`)

Generates the text brief, then renders it to a PDF in memory using ReportLab. Returned as `application/pdf` with `Content-Disposition: attachment`.

---

## Frontend architecture (`frontend/`)

Single HTML file with two JS modules. No build step, no framework.

### State model

```mermaid
stateDiagram-v2
    [*] --> URLMode: page load

    URLMode: URL mode\nappMode = url\nheroSection visible
    Ingesting: Ingesting\nprogressWrap visible\nSSE stream active
    OutputReady: Output ready\nappMode = chat\noutputPanel visible

    URLMode --> Ingesting: analyseBtn click\nstartIngestion()
    Ingesting --> OutputReady: SSE complete\nonIngestionComplete()
    OutputReady --> URLMode: newAnalysisBtn\nresetToUrlMode()
    OutputReady --> OutputReady: loadSession()\nfrom sidebar
```

```
localStorage['compass_sessions']: Session[]
  Session {
    sessionId, repoName, repoUrl, createdAt,
    textBrief,        ← full markdown string
    chatHistory[]     ← {role, text}[], capped at 40
  }
```

Sessions are the single source of truth for the sidebar. The server holds nothing the browser needs to restore a session — it only needs a live server for new chat messages.

### Ingestion flow (JS)

```mermaid
flowchart TD
    A["startIngestion()"] --> B["POST /ingest SSE"]
    B --> C["appendProgress(event, msg)"]
    C --> D["nav progress bar width"]
    C --> E["terminal log line\nev-* colour class"]
    B --> F{"SSE event = complete?"}
    F -->|no| C
    F -->|yes| G["onIngestionComplete()"]
    G --> H["GET /brief/text\nrenderMarkdown()"]
    G --> I["upsertSession()\nlocalStorage"]
    G --> J["activateChatMode()"]
    J --> K["show audioBtn + pdfBtn\nplaceholder → chat mode"]
```

### Audio pipeline (JS — `audio-manager.js`)

```mermaid
flowchart LR
    subgraph Capture
        MIC["getUserMedia\n16 kHz"] --> SPN["ScriptProcessorNode\n4096 samples"]
        SPN --> DS["downsample"] --> B64["base64 encode"]
    end

    subgraph Transport
        B64 -->|"WS {type:audio}"| WS["WebSocket"]
        WS -->|"{event:audio}"| RECV["receive frames"]
    end

    subgraph Playback
        RECV --> Q["frame queue"]
        Q --> JB{"buffered\n≥ 180ms?"}
        JB -->|no| Q
        JB -->|yes| SCHED["source.start(nextStartTime)\npre-scheduled timestamps"]
        SCHED --> AC["AudioContext 24kHz\ngapless output"]
    end
```

---

## Data flow at a glance

```mermaid
flowchart TD
    URL["GitHub URL"]
    Clone["clone_repo()\nshallow depth=1 → temp dir"]
    Read["read_repo()\nfilter binary/large/node_modules\n+ truncate_to_limit() 900k tokens"]
    Passes["run_all_passes()\n4× generate_content_stream"]
    Graph["knowledge graph\n{per_file, connections,\nfeatures, architecture}"]

    TextBrief["GET /brief/text\ngemini-3-flash-preview\n→ markdown string"]
    PDF["GET /brief/pdf\nReportLab → PDF bytes"]
    Chat["POST /chat\ngemini-3-flash-preview\nSSE text chunks"]
    Audio["WS /ws\nADK + Gemini Live\nbidi PCM audio"]

    URL --> Clone --> Read --> Passes --> Graph
    Graph --> TextBrief
    Graph --> PDF
    Graph --> Chat
    Graph --> Audio
```

---

## Limits and known constraints

| Constraint | Value | Where enforced |
|---|---|---|
| Max file size per repo file | 100 KB | `reader.py` |
| Context window target | 900 000 tokens | `reader.py:TOKEN_LIMIT` |
| Context budget for graph string | ~8 000 tokens | `graph.py:to_context_string()` |
| Chat history cap (server) | 40 entries | `conversations.py` |
| Chat history cap (client) | 40 entries | `main.js` |
| Sessions retained (localStorage) | 20 | `main.js:saveSessions()` |
| Context cache TTL | 600 s | `extractor.py` |
| Audio jitter buffer | 180 ms | `audio-manager.js:JITTER_MS` |
| Mic sample rate | 16 kHz | `audio-manager.js` |
| Playback sample rate | 24 kHz | `audio-manager.js` |

---

## What is not persistent

- **Knowledge graphs** — in-memory Python dict, lost on server restart
- **Conversation history** — in-memory Python dict, lost on server restart
- **ADK sessions** — `InMemorySessionService`, lost on server restart

The browser `localStorage` cache (text brief + chat history) means users can still read past briefs and chat history after a server restart; they just can't send new chat messages to sessions the server no longer holds.

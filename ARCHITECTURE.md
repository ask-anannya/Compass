# Compass — Architecture

## Overview

Compass is a single-server web application. FastAPI serves both the REST/WebSocket API and the static frontend from the same process. There is no database — all state is held in two in-memory Python dicts (knowledge graphs and conversation histories) and in the browser's `localStorage`.

```mermaid
graph TD
    Browser["Browser (vanilla JS)"]
    FastAPI["FastAPI (main.py)"]
    Ingestion["ingestion/\nclone → read → analyse"]
    Knowledge["knowledge/\nin-memory graph store"]
    Briefing["briefing/\ntext · audio · session · diagram · pdf · chat"]
    Gemini["Gemini API"]
    ADK["Google ADK\n(Gemini Live)"]
    MermaidInk["mermaid.ink\n(diagram render)"]

    Browser -->|"POST /ingest (SSE)"| FastAPI
    Browser -->|"GET /brief/text"| FastAPI
    Browser -->|"GET /brief/pdf"| FastAPI
    Browser -->|"GET /brief/diagram"| FastAPI
    Browser -->|"POST /chat (SSE)"| FastAPI
    Browser -->|"WS /brief/audio (bidi)"| FastAPI
    Browser -->|"WS /session/audio (bidi)"| FastAPI

    FastAPI --> Ingestion
    FastAPI --> Briefing
    Ingestion --> Knowledge
    Briefing --> Knowledge

    Ingestion -->|"generate_content_stream"| Gemini
    Briefing -->|"generate_content\ngenerate_content_stream"| Gemini
    Briefing -->|"StreamingMode.BIDI"| ADK
    Briefing -->|"GET /img/{encoded}"| MermaidInk
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

A module-level `dict[str, dict]` keyed by `session_id`. No persistence — graphs are lost on server restart. `to_context_string()` flattens the graph to a compact ~8 000-token string used as context injection for all downstream outputs (text brief, chat, audio brief, diagram).

> **Production note:** replace `_store` with Redis + 2-hour TTL.

### 4. Text brief (`GET /brief/text/{session_id}`)

Loads the graph, calls `to_context_string()`, and sends a single non-streaming `generate_content` call to `gemini-3-flash-preview` with a fixed formatting prompt. Returns `{ brief: string, graph: dict }`.

### 5. Chat (`POST /chat/{session_id}`)

SSE-streamed multi-turn chat grounded in the knowledge graph. Messages are inspected against `_PLAN_KEYWORDS` — if matched, the request is routed to `gemini-3.1-pro-preview` for a detailed implementation plan; otherwise `gemini-3-flash-preview` handles it.

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
    C->>C: route to flash or pro<br/>based on plan keywords
    C->>G: generate_content_stream(contents)
    G-->>C: text chunks
    C-->>R: yield chunk
    R-->>B: SSE: {event:chunk, text:...}
    R->>H: append_turn(model, full_response)
    R-->>B: SSE: {event:done}
```

Chat history is also persisted in `localStorage` on the client and replayed on session load, so the UI survives server restarts.

### 6. Live Assistant (`WebSocket /brief/audio/{session_id}`)

Uses Google ADK (`google-adk`) for bidirectional audio via Gemini Live. Four concurrent asyncio tasks run per session.

```mermaid
sequenceDiagram
    participant B as Browser
    participant WS as websocket route
    participant ADK as Google ADK
    participant G as Gemini Live

    B->>WS: WS connect /brief/audio/{session_id}
    WS->>ADK: create_session(adk_session_id)
    WS->>ADK: queue.send_content(knowledge_graph)
    Note over WS: asyncio.create_task × 4

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
            G-->>WS: event.content.parts (audio)
            WS-->>B: {event:audio, data:base64}
        end
        G-->>WS: event.output_transcription (spoken text)
        WS-->>B: {event:transcript, text}
        G-->>WS: turn_complete
        WS-->>B: {event:ready}
        G-->>WS: function_call (tool invoked by agent)
        ADK->>ADK: execute tool, send function_response
    and plan_task
        Note over WS: drains plan_queue
        WS-->>B: {event:plan_requested, text}
    and diagram_task
        Note over WS: drains diagram_queue
        WS-->>B: {event:diagram_requested}
    end

    Note over WS: asyncio.wait(FIRST_COMPLETED)<br/>cancels remaining tasks
```

**Tool calling:** The ADK agent (`briefing/agent.py`) has two registered tools:
- `request_implementation_plan(description)` — puts description onto a `ContextVar`-backed `asyncio.Queue`; `plan_task` drains it and emits `plan_requested` over the WebSocket; frontend calls `sendMessage(text)` to trigger the full chat pipeline
- `request_architecture_diagram()` — same pattern; frontend calls `generateDiagram()`

**Transcript:** Uses `event.output_transcription` (server-side transcription of synthesised audio) rather than `event.content.parts[].text` (which includes internal reasoning). Partial chunks are streamed in real time; the final `finished=True` event is skipped to avoid duplication.

**Interruption:** All scheduled `AudioBufferSourceNode`s are tracked in `activeSources[]`. On `interrupted` event or manual stop, `.stop()` is called on each — eliminating residual audio that `suspend/resume` tricks cannot cancel. Echo cancellation on the mic prevents the model's own speaker output from triggering false VAD detections.

- **Model**: `gemini-2.5-flash-native-audio-preview-12-2025`
- **Streaming mode**: `StreamingMode.BIDI` — full-duplex
- **Voice**: Charon (prebuilt)
- **Transcript**: `output_audio_transcription` enabled by default in `RunConfig`

### 7. Architecture Diagram (`GET /brief/diagram/{session_id}`)

Loads the knowledge graph and builds a structured prompt from it (architecture pattern, framework, features as subsystems, key files as leaf nodes). Sends it to `gemini-3-pro-image-preview` with `response_modalities=['IMAGE']`. Returns `{ image: base64, mime_type: "image/jpeg" }`.

The frontend embeds it as a `data:` URL in an AI chat bubble. Clicking the image opens a fullscreen lightbox overlay; `Escape` or clicking outside closes it.

### 8. Ambient Session (`WebSocket /session/audio/{session_id}`)

A proactive AI observer that watches the user's screen and listens to mic audio while they code. Runs as a separate ADK agent (`compass_session` / `briefing/session_agent.py`) with its own `InMemorySessionService` to avoid session ID collisions with the Live Assistant.

```mermaid
sequenceDiagram
    participant B as Browser
    participant WS as session route
    participant ADK as Google ADK
    participant G as Gemini Live

    B->>WS: WS connect /session/audio/{session_id}
    WS->>ADK: create_session(compass_session_<id>)
    WS->>ADK: queue.send_content(knowledge_graph + active_plan)
    Note over WS: asyncio.create_task × 4

    par upstream_task
        loop mic + screen
            B->>WS: {type:audio, data:base64_pcm_16k}
            WS->>ADK: queue.send_realtime(Blob pcm/16000)
            B->>WS: {type:frame, data:base64_jpeg}
            WS->>ADK: queue.send_realtime(Blob image/jpeg)
        end
        B->>WS: {type:stop}
    and downstream_task
        ADK->>G: runner.run_live(BIDI, proactive_audio=True)
        G-->>WS: audio + transcription events
        WS-->>B: {event:audio} / {event:transcript}
    and plan_task
        WS-->>B: {event:plan_requested, text}
    and diagram_task
        WS-->>B: {event:diagram_requested}
    end
```

**Key details:**
- `extract_latest_plan()` walks the chat history in reverse to find the most recent model response preceded by a plan-keyword message; injected into the initial context prompt
- `ProactivityConfig(proactive_audio=True)` — Gemini speaks without waiting for a user turn
- Screen is captured at 1 fps, 1280×720, JPEG quality 0.7, via `getDisplayMedia` + canvas `setInterval`
- A silent looping `AudioContext` acts as a keep-alive to prevent Chrome from throttling the tab

### 9. PDF (`GET /brief/pdf/{session_id}`)

Generates a multi-page PDF in memory using ReportLab. Returned as `application/pdf` with `Content-Disposition: attachment`.

| Page | Content |
|---|---|
| 1 | Black cover — Compass logo (PIL-inverted PNG), "COMPASS" wordmark, subtitle |
| 2 | Architecture Overview — `before_you_touch_anything`, conventions, rules |
| 3 | Architecture Diagram — Mermaid `flowchart TD` built from the knowledge graph, rendered to PNG via `mermaid.ink` GET API |
| 4 | Feature Map — table of all features with entry points, files, descriptions |
| 5+ | File Breakdown — every file with purpose, callers, callees, key functions |

The Mermaid diagram is constructed deterministically from the graph: entry points as a top cluster, one subgraph per feature listing up to 3 files, edges derived from `arch.entry_points` → features and cross-feature calls from the `connections` array. File node IDs are prefixed with their feature ID to prevent Mermaid parse errors from duplicate IDs.

---

## Frontend architecture (`frontend/`)

Single HTML file with three JS modules. No build step, no framework.

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

### Live Assistant pipeline (JS — `audio-manager.js`)

```mermaid
flowchart LR
    subgraph Capture
        MIC["getUserMedia\n16 kHz\nechoCancellation=true"] --> SPN["ScriptProcessorNode\n4096 samples"]
        SPN --> PCM["float→int16"] --> B64["base64 encode"]
    end

    subgraph Transport
        B64 -->|"WS {type:audio}"| WS["WebSocket"]
        WS -->|"{event:audio}"| RECV["receive frames"]
        WS -->|"{event:transcript}"| TR["append to bubble"]
        WS -->|"{event:plan_requested}"| PLAN["sendMessage(text)"]
        WS -->|"{event:diagram_requested}"| DIAG["generateDiagram()"]
    end

    subgraph Playback
        RECV --> Q["frame queue + jitter buffer\n180 ms hold"]
        Q --> SCHED["source.start(nextStartTime)\npre-scheduled timestamps"]
        SCHED --> TRACK["activeSources[]\nstop() on interrupt"]
        TRACK --> AC["AudioContext 24kHz\ngapless output"]
    end
```

### Ambient Session pipeline (JS — `session-manager.js`)

`SessionManager` mirrors `AudioManager`'s audio engine but adds screen capture and a keep-alive oscillator:

```mermaid
flowchart TD
    subgraph Capture
        DISP["getDisplayMedia()\nvideo track"] --> CANVAS["canvas 1280×720\nsetInterval 1000ms"]
        CANVAS --> JPEG["toDataURL JPEG 0.7\n→ base64"] -->|"{type:frame}"| WS
        MIC2["getUserMedia\n16 kHz echoCancellation"] --> SPN2["ScriptProcessorNode"] --> PCM2["float→int16 → base64"] -->|"{type:audio}"| WS
    end

    subgraph KeepAlive
        OSC["OscillatorNode\ngain=0 → silent loop"] --> AC2["AudioContext\n(prevents tab throttle)"]
    end

    subgraph Playback
        WS -->|"{event:audio}"| Q2["jitter buffer 180ms"]
        Q2 --> SCHED2["source.start(nextStartTime)"]
        SCHED2 --> TRACK2["activeSources[]\nstop() on interrupt"]
        TRACK2 --> AC2
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
    PDF["GET /brief/pdf\nReportLab + mermaid.ink\n→ PDF bytes"]
    Chat["POST /chat\nflash (general) or pro (plans)\nSSE text chunks"]
    Audio["WS /brief/audio\nADK + Gemini Live\nbidi PCM audio + tools"]
    Diagram["GET /brief/diagram\ngemini-3-pro-image-preview\n→ JPEG image"]
    Session["WS /session/audio\nADK + Gemini Live\nscreen frames + mic + proactivity"]

    URL --> Clone --> Read --> Passes --> Graph
    Graph --> TextBrief
    Graph --> PDF
    Graph --> Chat
    Graph --> Audio
    Graph --> Diagram
    Graph --> Session
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

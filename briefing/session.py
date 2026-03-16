import asyncio
import base64
from google.genai import types
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from knowledge.graph import load, to_context_string
from knowledge.conversations import get_history
from briefing.chat import _PLAN_KEYWORDS
from briefing.agent import _plan_queue, _diagram_queue
from briefing.session_agent import session_runner

SESSION_PREFIX = 'compass_session_'


def extract_latest_plan(session_id: str) -> str | None:
    """Return the most recent implementation plan from chat history, or None."""
    history = get_history(session_id)
    for i in range(len(history) - 1, 0, -1):
        turn = history[i]
        if turn['role'] == 'model':
            prev = history[i - 1]
            if prev['role'] == 'user' and any(kw in prev['text'].lower() for kw in _PLAN_KEYWORDS):
                return turn['text']
    return None


async def run_ambient_session(session_id: str, websocket):
    graph = load(session_id)
    if not graph:
        await websocket.send_json({'event': 'error', 'msg': 'Session not found'})
        return

    knowledge   = to_context_string(graph)
    latest_plan = extract_latest_plan(session_id)
    adk_session_id = f'{SESSION_PREFIX}{session_id}'

    await session_runner.session_service.create_session(
        app_name='compass_session',
        user_id='compass',
        session_id=adk_session_id,
    )
    print(f'[session] ADK session created: {adk_session_id}')
    if latest_plan:
        print(f'[session] Active implementation plan found — injecting as reference')

    plan_queue    = asyncio.Queue()
    diagram_queue = asyncio.Queue()
    _plan_queue.set(plan_queue)
    _diagram_queue.set(diagram_queue)

    queue = LiveRequestQueue()

    run_config = RunConfig(
        response_modalities=['AUDIO'],
        streaming_mode=StreamingMode.BIDI,
        proactivity=types.ProactivityConfig(proactive_audio=True),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Charon')
            )
        ),
    )

    # Build initial context prompt
    initial_text = f"Here is the complete codebase knowledge graph:\n\n{knowledge}"
    if latest_plan:
        initial_text += f"\n\nACTIVE IMPLEMENTATION PLAN (generated in chat — use this as your reference):\n{latest_plan}"
    initial_text += (
        "\n\nSession started. Watch the screen and listen. "
        "Stay completely silent unless you have something specific and actionable to say. "
        "If an active plan is present, immediately flag any deviation from it."
    )

    queue.send_content(
        types.Content(parts=[types.Part(text=initial_text)])
    )
    print('[session] initial prompt queued')

    async def upstream_task():
        audio_count = 0
        frame_count = 0
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get('type') == 'audio':
                    audio_count += 1
                    if audio_count % 50 == 1:
                        print(f'[session upstream] audio chunks received: {audio_count}')
                    queue.send_realtime(types.Blob(
                        mime_type='audio/pcm;rate=16000',
                        data=base64.b64decode(msg['data']),
                    ))
                elif msg.get('type') == 'frame':
                    frame_count += 1
                    print(f'[session upstream] frame #{frame_count} received ({len(msg["data"])} chars b64)')
                    queue.send_realtime(types.Blob(
                        mime_type='image/jpeg',
                        data=base64.b64decode(msg['data']),
                    ))
                elif msg.get('type') == 'stop':
                    print(f'[session upstream] stop received — audio={audio_count} frames={frame_count}')
                    break
        except Exception as e:
            print(f'[session upstream] ended: {e}')
        finally:
            queue.close()

    async def downstream_task():
        event_count = 0
        try:
            print('[session downstream] starting run_live loop')
            async for event in session_runner.run_live(
                user_id='compass',
                session_id=adk_session_id,
                live_request_queue=queue,
                run_config=run_config,
            ):
                event_count += 1
                print(f'[session event #{event_count}] turn_complete={event.turn_complete} interrupted={event.interrupted} has_content={bool(event.content)} has_transcription={bool(event.output_transcription)}')

                if event.interrupted:
                    print('[session] interrupted — flushing')
                    await websocket.send_json({'event': 'interrupted'})
                    continue

                if event.turn_complete:
                    print('[session] turn_complete — sending ready')
                    await websocket.send_json({'event': 'ready'})
                    continue

                if event.output_transcription and event.output_transcription.text:
                    print(f'[session transcript] finished={event.output_transcription.finished} text={event.output_transcription.text!r}')
                    if not event.output_transcription.finished:
                        await websocket.send_json({'event': 'transcript', 'text': event.output_transcription.text})

                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith('audio'):
                        print(f'[session audio] sending {len(part.inline_data.data)} bytes')
                        await websocket.send_json({
                            'event': 'audio',
                            'data': base64.b64encode(part.inline_data.data).decode('utf-8'),
                        })
                    elif part.text:
                        print(f'[session text part] {part.text!r}')
        except Exception as e:
            import traceback
            print(f'[session downstream] ended with exception: {e}')
            traceback.print_exc()
        finally:
            print(f'[session downstream] exited after {event_count} events')

    async def plan_task():
        try:
            while True:
                description = await plan_queue.get()
                await websocket.send_json({'event': 'plan_requested', 'text': description})
        except Exception as e:
            print(f'[session plan_task] ended: {e}')

    async def diagram_task():
        try:
            while True:
                await diagram_queue.get()
                await websocket.send_json({'event': 'diagram_requested'})
        except Exception as e:
            print(f'[session diagram_task] ended: {e}')

    up      = asyncio.create_task(upstream_task())
    down    = asyncio.create_task(downstream_task())
    plan    = asyncio.create_task(plan_task())
    diagram = asyncio.create_task(diagram_task())

    done, pending = await asyncio.wait([up, down, plan, diagram], return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    print('[session] session closed')

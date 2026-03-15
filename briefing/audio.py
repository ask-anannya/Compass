import asyncio
import base64
from google.genai import types
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from knowledge.graph import load, to_context_string
from briefing.agent import runner, _plan_queue, _diagram_queue

SESSION_PREFIX = 'compass_'


async def run_audio_brief(session_id: str, websocket):
    graph = load(session_id)
    if not graph:
        await websocket.send_json({'event': 'error', 'msg': 'Session not found'})
        return

    knowledge = to_context_string(graph)
    adk_session_id = f'{SESSION_PREFIX}{session_id}'

    await runner.session_service.create_session(
        app_name='compass',
        user_id='compass',
        session_id=adk_session_id,
    )
    print(f'[audio] ADK session created: {adk_session_id}')

    plan_queue    = asyncio.Queue()
    diagram_queue = asyncio.Queue()
    _plan_queue.set(plan_queue)
    _diagram_queue.set(diagram_queue)

    queue = LiveRequestQueue()

    run_config = RunConfig(
        response_modalities=['AUDIO'],
        streaming_mode=StreamingMode.BIDI,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Charon')
            )
        ),
    )

    queue.send_content(
        types.Content(parts=[types.Part(
            text=f"Here is the complete codebase knowledge graph:\n\n{knowledge}\n\nPlease begin the briefing."
        )])
    )
    print('[audio] initial prompt queued')

    async def upstream_task():
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get('type') == 'audio':
                    queue.send_realtime(types.Blob(
                        mime_type='audio/pcm;rate=16000',
                        data=base64.b64decode(msg['data']),
                    ))
                elif msg.get('type') == 'stop':
                    break
        except Exception as e:
            print(f'[upstream] ended: {e}')
        finally:
            queue.close()

    async def downstream_task():
        try:
            async for event in runner.run_live(
                user_id='compass',
                session_id=adk_session_id,
                live_request_queue=queue,
                run_config=run_config,
            ):
                print(f'[event] turn_complete={event.turn_complete} interrupted={event.interrupted} has_content={bool(event.content)}')

                if event.interrupted:
                    await websocket.send_json({'event': 'interrupted'})
                    continue

                if event.turn_complete:
                    await websocket.send_json({'event': 'ready'})
                    continue

                if (event.output_transcription
                        and event.output_transcription.text
                        and not event.output_transcription.finished):
                    print(f'[transcript] {event.output_transcription.text}')
                    await websocket.send_json({'event': 'transcript', 'text': event.output_transcription.text})

                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith('audio'):
                        await websocket.send_json({
                            'event': 'audio',
                            'data': base64.b64encode(part.inline_data.data).decode('utf-8'),
                        })
        except Exception as e:
            print(f'[downstream] ended: {e}')

    async def plan_task():
        try:
            while True:
                description = await plan_queue.get()
                print(f'[plan_task] sending plan_requested: {description!r}')
                await websocket.send_json({'event': 'plan_requested', 'text': description})
        except Exception as e:
            print(f'[plan_task] ended: {e}')

    async def diagram_task():
        try:
            while True:
                await diagram_queue.get()
                await websocket.send_json({'event': 'diagram_requested'})
        except Exception as e:
            print(f'[diagram_task] ended: {e}')

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

    print('[audio] session closed')

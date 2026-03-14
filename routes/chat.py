import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from briefing.chat import stream_chat_response
from knowledge.conversations import append_turn

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


@router.post('/chat/{session_id}')
async def chat(session_id: str, req: ChatRequest):
    append_turn(session_id, 'user', req.message)

    async def event_stream():
        full_response = ''
        try:
            async for chunk in stream_chat_response(session_id, req.message):
                full_response += chunk
                yield f"data: {json.dumps({'event': 'chunk', 'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'text': str(e)})}\n\n"
        finally:
            if full_response:
                append_turn(session_id, 'model', full_response)
            yield f"data: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )

from fastapi import APIRouter, WebSocket
from briefing.session import run_ambient_session

router = APIRouter()


@router.websocket('/session/audio/{session_id}')
async def session_audio(websocket: WebSocket, session_id: str):
    await websocket.accept()
    await run_ambient_session(session_id, websocket)

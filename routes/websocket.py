from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from briefing.audio import run_audio_brief

router = APIRouter()


@router.websocket('/brief/audio/{session_id}')
async def audio_brief(websocket: WebSocket, session_id: str):
    print(f"[WS] Connection attempt for session: {session_id}")
    await websocket.accept()
    print(f"[WS] Accepted — 101 sent")
    try:
        await run_audio_brief(session_id, websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({'event': 'error', 'msg': str(e)})
        except Exception:
            pass

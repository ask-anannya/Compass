import base64
from fastapi import APIRouter, HTTPException
from briefing.diagram import generate_diagram

router = APIRouter()


@router.get('/brief/diagram/{session_id}')
async def get_diagram(session_id: str):
    try:
        image_bytes, mime_type = await generate_diagram(session_id)
        return {
            'image':     base64.b64encode(image_bytes).decode('utf-8'),
            'mime_type': mime_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

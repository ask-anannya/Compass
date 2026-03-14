from fastapi import APIRouter, HTTPException
from briefing.text import generate_text_brief
from knowledge.graph import load

router = APIRouter()


@router.get('/brief/text/{session_id}')
async def get_text_brief(session_id: str):
    try:
        text  = await generate_text_brief(session_id)
        graph = load(session_id) or {}
        return {'brief': text, 'graph': graph}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

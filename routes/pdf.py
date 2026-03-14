from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from briefing.pdf import generate_pdf

router = APIRouter()


@router.get('/brief/pdf/{session_id}')
def get_pdf(session_id: str):
    try:
        pdf_bytes = generate_pdf(session_id)
        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="compass-report-{session_id[:8]}.pdf"'}
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

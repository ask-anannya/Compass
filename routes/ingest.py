import uuid
import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ingestion.cloner import clone_repo, cleanup
from ingestion.reader import read_repo, truncate_to_limit, estimate_tokens
from ingestion.extractor import run_all_passes
from knowledge.graph import save

router = APIRouter()


class IngestRequest(BaseModel):
    github_url: str


@router.post('/ingest')
async def ingest(req: IngestRequest):
    session_id = str(uuid.uuid4())

    async def event_stream():
        repo_path = None
        progress_queue = asyncio.Queue()
        extraction_task = None

        try:
            yield f"data: {json.dumps({'event': 'start', 'session_id': session_id})}\n\n"

            yield f"data: {json.dumps({'event': 'cloning', 'msg': 'Cloning repository...'})}\n\n"
            repo_path = clone_repo(req.github_url)

            yield f"data: {json.dumps({'event': 'reading', 'msg': 'Reading files...'})}\n\n"
            files = read_repo(repo_path)
            files, was_truncated = truncate_to_limit(files)
            token_estimate = estimate_tokens(files)

            yield f"data: {json.dumps({'event': 'reading', 'msg': f'Found {len(files)} files (~{token_estimate:,} tokens)'})}\n\n"

            if was_truncated:
                yield f"data: {json.dumps({'event': 'warning', 'msg': 'Large repo: some deep files excluded to fit context window'})}\n\n"

            # Launch extraction concurrently so we can stream its progress via the queue
            extraction_task = asyncio.create_task(
                run_all_passes(files, progress_queue=progress_queue)
            )

            # Drain the queue until extraction finishes
            while not extraction_task.done():
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    continue  # heartbeat keeps SSE connection alive

            # Drain any remaining queued events
            while not progress_queue.empty():
                event = progress_queue.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"

            graph = extraction_task.result()  # raises if extraction threw
            save(session_id, graph)

            yield f"data: {json.dumps({'event': 'complete', 'session_id': session_id})}\n\n"

        except ValueError as e:
            yield f"data: {json.dumps({'event': 'error', 'msg': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'msg': f'Extraction failed: {e}'})}\n\n"
            if extraction_task and not extraction_task.done():
                extraction_task.cancel()
        finally:
            if repo_path:
                cleanup(repo_path)

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  # prevents nginx / Cloud Run proxy from buffering SSE
        }
    )

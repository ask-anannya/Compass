import json
import os
import re
import asyncio
from google import genai
from google.genai import types

client = genai.Client()
MODEL  = 'gemini-3-flash-preview'


# ── Prompts ───────────────────────────────────────────────────────────────────

PASS1_PROMPT = """
Analyse every file in this repository.
For each file return a JSON object with:
{
  "path": "relative/path",
  "purpose": "one sentence: what this file does",
  "functions": ["all function and class names defined here"],
  "imports": ["everything imported or required"],
  "exports": ["names other files would import from this"],
  "key_logic": "2-3 sentences on the most important logic"
}
Return a JSON array of these objects. No prose. No markdown fences.
"""

PASS2_PROMPT = """
Using the repository, map how files connect to each other.
Return a JSON array where each entry is:
{
  "path": "relative/path",
  "calls": ["paths of files this file directly calls or imports from"],
  "called_by": ["paths of files that import or call this file"],
  "data_flow": "one sentence: what data flows in and out of this file"
}
Return only the JSON array. No prose. No markdown fences.
"""

PASS3_PROMPT = """
Identify the major features or subsystems in this codebase.
For each feature return:
{
  "feature": "name of the feature (e.g. 'authentication', 'payment processing')",
  "files": ["all file paths involved in this feature"],
  "entry_point": "the file where this feature starts / is first called",
  "description": "2-3 sentences on how this feature works end-to-end"
}
Return a JSON array. No prose. No markdown fences.
"""

PASS4_PROMPT = """
Synthesise everything you have read into an architectural overview.
Return a single JSON object:
{
  "pattern": "the overall architecture pattern (e.g. MVC, layered, event-driven)",
  "language": "primary language(s)",
  "framework": "primary framework(s)",
  "entry_points": ["main entry point files"],
  "conventions": ["list of coding/naming conventions used throughout"],
  "rules": ["important constraints a contributor must know (e.g. 'never import X from Y')"],
  "before_you_touch_anything": "the 3-5 most important things to know as a new contributor"
}
No prose. No markdown fences.
"""


# ── Streaming entity patterns per pass ────────────────────────────────────────

_PATTERNS = {
    'pass_1': (r'"path"\s*:\s*"([^"]+)"',    lambda m: m),
    'pass_2': (r'"path"\s*:\s*"([^"]+)"',    lambda m: m),
    'pass_3': (r'"feature"\s*:\s*"([^"]+)"', lambda m: f'Feature: {m}'),
    'pass_4': (
        r'"(?:pattern|language|framework)"\s*:\s*"([^"]+)"',
        lambda m: m,
    ),
}


# ── Core pass runner ──────────────────────────────────────────────────────────

async def run_pass(
    prompt: str,
    repo_content: str = '',
    cache_name: str = None,
    progress_queue: asyncio.Queue = None,
    event_type: str = None,
) -> dict | list:
    parts = []
    if repo_content:
        parts.append(types.Part.from_text(text=repo_content))
    parts.append(types.Part.from_text(text=prompt))

    config = types.GenerateContentConfig(
        response_mime_type='application/json',
        cached_content=cache_name
    )

    accumulated = ''
    seen = set()
    pattern, formatter = _PATTERNS.get(event_type, (None, None)) if event_type else (None, None)

    async for chunk in await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=[types.Content(role='user', parts=parts)],
        config=config,
    ):
        if not chunk.text:
            continue
        accumulated += chunk.text

        if progress_queue and pattern:
            for match in re.finditer(pattern, accumulated):
                value = match.group(1)
                if value not in seen:
                    seen.add(value)
                    await progress_queue.put({'event': event_type, 'msg': formatter(value)})

    return json.loads(accumulated)


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_all_passes(repo_files: list[dict], progress_queue: asyncio.Queue = None) -> dict:
    """
    Pack repo, cache it, run all 4 passes.
    Puts {'event': str, 'msg': str} dicts into progress_queue between passes.
    Returns complete knowledge graph.
    """
    repo_content = pack_files(repo_files)

    async def emit(event: str, msg: str):
        if progress_queue:
            await progress_queue.put({'event': event, 'msg': msg})

    await emit('pass_1', 'Extracting per-file summaries...')
    per_file = await run_pass(
        PASS1_PROMPT,
        repo_content=repo_content,
        progress_queue=progress_queue,
        event_type='pass_1',
    )

    # Create context cache after Pass 1 — reuse for Passes 2-4
    cache = None
    cache_name = None
    try:
        cache = await client.aio.caches.create(
            model=MODEL,
            contents=[types.Content(role='user', parts=[types.Part.from_text(text=repo_content)])],
            config=types.CreateCachedContentConfig(ttl='600s')
        )
        cache_name = cache.name
    except Exception as e:
        print(f"Context cache unavailable, continuing without: {e}")

    try:
        await emit('pass_2', 'Mapping file connections...')
        connections = await run_pass(
            PASS2_PROMPT,
            repo_content='' if cache_name else repo_content,
            cache_name=cache_name,
            progress_queue=progress_queue,
            event_type='pass_2',
        )

        await emit('pass_3', 'Identifying features...')
        features = await run_pass(
            PASS3_PROMPT,
            repo_content='' if cache_name else repo_content,
            cache_name=cache_name,
            progress_queue=progress_queue,
            event_type='pass_3',
        )

        await emit('pass_4', 'Synthesising architecture...')
        architecture = await run_pass(
            PASS4_PROMPT,
            repo_content='' if cache_name else repo_content,
            cache_name=cache_name,
            progress_queue=progress_queue,
            event_type='pass_4',
        )
        if isinstance(architecture, list):
            architecture = architecture[0]
    finally:
        if cache:
            try:
                await client.aio.caches.delete(name=cache.name)
            except Exception:
                pass  # best-effort cleanup

    return {
        'per_file':     per_file,
        'connections':  connections,
        'features':     features,
        'architecture': architecture
    }


# ── File packing ──────────────────────────────────────────────────────────────

def pack_files(files: list[dict]) -> str:
    priority_names = {
        'readme.md', 'readme', 'readme.txt',
        'package.json', 'requirements.txt', 'setup.py',
        'pyproject.toml', 'go.mod', 'cargo.toml', 'makefile',
        'docker-compose.yml', 'docker-compose.yaml'
    }

    def sort_key(f):
        name = os.path.basename(f['path']).lower()
        return (0 if name in priority_names else 1, f['path'])

    sorted_files = sorted(files, key=sort_key)
    return '\n'.join(f"=== FILE: {f['path']} ===\n{f['content']}\n" for f in sorted_files)

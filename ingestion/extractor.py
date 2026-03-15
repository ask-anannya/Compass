import json
import os
import re
import asyncio
from google import genai
from google.genai import types
from ingestion.reader import reduce_for_large_repo

client = genai.Client()
MODEL  = 'gemini-3-flash-preview'

CHARS_PER_TOKEN   = 4          # must match reader.py
BATCH_TOKEN_LIMIT = 700_000    # per-batch ceiling for large-repo path


# ── Prompts (standard — used for repos ≤ 700k tokens) ────────────────────────

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


# ── Compact prompt variants (large-repo path — Passes 2-4 only) ───────────────
# Identical to originals with a preamble telling Gemini it's reading structured
# summaries rather than raw source.

_COMPACT_PREAMBLE = """
You are given structured per-file summaries of a codebase extracted during a
first-pass analysis, not the raw source code. Treat the "functions", "imports",
and "exports" fields as ground truth for inferring call relationships.
"""

PASS2_PROMPT_COMPACT = _COMPACT_PREAMBLE + PASS2_PROMPT
PASS3_PROMPT_COMPACT = _COMPACT_PREAMBLE + PASS3_PROMPT
PASS4_PROMPT_COMPACT = _COMPACT_PREAMBLE + PASS4_PROMPT


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def estimate_tokens_str(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN


def build_compact_context(per_file: list) -> str:
    """Serialize per_file[] as compact JSON context for Passes 2-4."""
    return (
        '=== CODEBASE KNOWLEDGE (per-file summaries from Pass 1) ===\n'
        + json.dumps(per_file, indent=2)
    )


def split_into_batches(files: list[dict]) -> list[list[dict]]:
    """
    Greedy bin-packing into batches of at most BATCH_TOKEN_LIMIT tokens.
    Priority files (readme, package.json, etc.) are always placed in batch 0.
    """
    priority_names = {
        'readme.md', 'readme', 'readme.txt',
        'package.json', 'requirements.txt', 'setup.py',
        'pyproject.toml', 'go.mod', 'cargo.toml', 'makefile',
        'docker-compose.yml', 'docker-compose.yaml'
    }

    priority = [f for f in files if os.path.basename(f['path']).lower() in priority_names]
    rest     = [f for f in files if os.path.basename(f['path']).lower() not in priority_names]

    batches  = [[]]
    running  = 0

    # Priority files always go in batch 0
    for f in priority:
        tokens   = len(f['content']) // CHARS_PER_TOKEN
        batches[0].append(f)
        running += tokens

    for f in rest:
        tokens = len(f['content']) // CHARS_PER_TOKEN
        if running + tokens > BATCH_TOKEN_LIMIT:
            batches.append([])
            running = 0
        batches[-1].append(f)
        running += tokens

    return [b for b in batches if b]  # drop any empty batches


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


# ── Batched Pass 1 (large-repo path only) ─────────────────────────────────────

async def run_pass1_batched(
    files: list[dict],
    progress_queue: asyncio.Queue = None,
) -> list:
    """
    Run Pass 1 in sequential batches and merge results.
    Only called when total tokens exceed BATCH_TOKEN_LIMIT.
    """
    batches = split_into_batches(files)
    n       = len(batches)
    results = []

    async def emit(event, msg):
        if progress_queue:
            await progress_queue.put({'event': event, 'msg': msg})

    for i, batch in enumerate(batches, 1):
        await emit('pass_1', f'Batch {i}/{n} — {len(batch)} files')

        batch_prompt = (
            f'Note: this is batch {i} of {n} of a larger repository. '
            f'Other files exist outside this batch.\n'
            + PASS1_PROMPT
        )
        batch_content = pack_files(batch)

        batch_result = await run_pass(
            batch_prompt,
            repo_content=batch_content,
            progress_queue=progress_queue,
            event_type='pass_1',
        )

        if isinstance(batch_result, list):
            results.extend(batch_result)

        if i < n:
            await emit('pass_1', f'[batch {i}/{n} complete — {len(results)} files so far]')

    return results


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_all_passes(repo_files: list[dict], progress_queue: asyncio.Queue = None) -> dict:
    """
    Run all 4 passes. For repos ≤ 700k tokens the path is identical to the
    original implementation. For larger repos the large-repo fallback activates:
    Pass 1 is batched, Passes 2-4 use the compact per_file[] context.
    """
    repo_content = pack_files(repo_files)
    is_large     = estimate_tokens_str(repo_content) > BATCH_TOKEN_LIMIT

    async def emit(event: str, msg: str):
        if progress_queue:
            await progress_queue.put({'event': event, 'msg': msg})

    # ── Pass 1 ────────────────────────────────────────────────────────────────
    await emit('pass_1', 'Extracting per-file summaries...')

    if is_large:
        # Reduce: drop noisy dirs, skeletonise large files — large-repo path only
        reduced_files, skel_count = reduce_for_large_repo(repo_files)
        if skel_count:
            await emit('warning', f'{skel_count} large files skeletonised (signatures only)')
        per_file    = await run_pass1_batched(reduced_files, progress_queue)
        p24_content = build_compact_context(per_file)
    else:
        # Small repo — current behaviour, untouched
        per_file    = await run_pass(
            PASS1_PROMPT,
            repo_content=repo_content,
            progress_queue=progress_queue,
            event_type='pass_1',
        )
        p24_content = repo_content

    # ── Context cache ─────────────────────────────────────────────────────────
    # Small repos: caches raw content (existing behaviour)
    # Large repos: caches compact context (smaller, more likely to succeed)
    cache      = None
    cache_name = None
    try:
        cache = await client.aio.caches.create(
            model=MODEL,
            contents=[types.Content(role='user', parts=[types.Part.from_text(text=p24_content)])],
            config=types.CreateCachedContentConfig(ttl='600s')
        )
        cache_name = cache.name
    except Exception as e:
        print(f"Context cache unavailable, continuing without: {e}")

    try:
        # ── Passes 2-4 ────────────────────────────────────────────────────────
        # Small repos: p24_content == repo_content  → identical to current code
        # Large repos: p24_content == compact JSON  → large-repo fallback
        await emit('pass_2', 'Mapping file connections...')
        connections = await run_pass(
            PASS2_PROMPT if not is_large else PASS2_PROMPT_COMPACT,
            repo_content='' if cache_name else p24_content,
            cache_name=cache_name,
            progress_queue=progress_queue,
            event_type='pass_2',
        )

        await emit('pass_3', 'Identifying features...')
        features = await run_pass(
            PASS3_PROMPT if not is_large else PASS3_PROMPT_COMPACT,
            repo_content='' if cache_name else p24_content,
            cache_name=cache_name,
            progress_queue=progress_queue,
            event_type='pass_3',
        )

        await emit('pass_4', 'Synthesising architecture...')
        architecture = await run_pass(
            PASS4_PROMPT if not is_large else PASS4_PROMPT_COMPACT,
            repo_content='' if cache_name else p24_content,
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
                pass

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

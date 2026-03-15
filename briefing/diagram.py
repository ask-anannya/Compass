from google import genai
from google.genai import types
from knowledge.graph import load

client = genai.Client()
MODEL  = 'gemini-3-pro-image-preview'


def _build_prompt(graph: dict) -> str:
    arch = graph['architecture']
    if isinstance(arch, list):
        arch = arch[0]

    features = '\n'.join(f"  - {f['feature']}: {f['description']}" for f in graph['features'])
    files     = '\n'.join(f"  {f['path']}: {f['purpose']}" for f in graph['per_file'])
    entries   = ', '.join(arch['entry_points'])

    return f"""Create a professional software architecture diagram for this codebase.

System: {arch['pattern']} — {arch['framework']} ({arch['language']})
Entry points: {entries}

Subsystems / Features:
{features}

Key files (path: purpose):
{files}

Diagram requirements:
- Label every component clearly
- Use arrows to show data flow and dependencies between components
- Group components by layer: frontend, backend, storage/external services
- Clean, minimal style — dark background, white/coloured boxes, modern tech aesthetic
- No decorative elements, focus on accuracy and clarity
"""


async def generate_diagram(session_id: str) -> tuple[bytes, str]:
    graph = load(session_id)
    if not graph:
        raise ValueError('Session not found')

    prompt = _build_prompt(graph)

    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=['IMAGE'],
        ),
    )

    print(f'[diagram] candidates: {len(response.candidates) if response.candidates else 0}')
    if response.candidates:
        c = response.candidates[0]
        print(f'[diagram] finish_reason={c.finish_reason}  content={c.content}')
        if c.content and c.content.parts:
            for part in c.content.parts:
                print(f'[diagram] part type: inline_data={bool(part.inline_data)} text={bool(part.text)}')
                if part.inline_data:
                    return part.inline_data.data, part.inline_data.mime_type

    raise ValueError('Model returned no image — check server logs for finish_reason')

from google import genai
from google.genai import types
from knowledge.graph import load, to_context_string

client = genai.Client()
MODEL  = 'gemini-3-flash-preview'

TEXT_BRIEF_PROMPT = """
Write a concise codebase brief for a developer joining this project.

Structure it exactly as:

## What This Codebase Does
[1-2 sentences]

## Architecture
[2-3 sentences on the pattern, framework, key design decisions]

## The Files That Matter Most
[bullet list of 5-7 most important files with one-line descriptions]

## Key Conventions
[bullet list of 3-5 conventions enforced throughout the code]

## Before You Touch Anything
[numbered list of 3-5 things that will save you hours]

## Feature Map
[bullet list: feature name → entry point file]

Keep it tight. A developer should be able to read this in 90 seconds.
"""


async def generate_text_brief(session_id: str) -> str:
    graph = load(session_id)
    if not graph:
        raise ValueError('Session not found')

    knowledge = to_context_string(graph)

    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=[types.Content(role='user', parts=[
            types.Part.from_text(text=f"Here is the codebase knowledge graph:\n\n{knowledge}"),
            types.Part.from_text(text=TEXT_BRIEF_PROMPT)
        ])]
        # No thinking_config — thinking is on by default for gemini-3-flash-preview
        # and this is a lightweight formatting pass anyway
    )

    return response.text

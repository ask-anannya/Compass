from google import genai
from google.genai import types
from knowledge.graph import load, to_context_string
from knowledge.conversations import get_history

client = genai.Client()
MODEL_CHAT = 'gemini-3-flash-preview'
MODEL_PLAN = 'gemini-3.1-pro-preview'

_PLAN_KEYWORDS = (
    'implementation plan', 'implement', 'how to add', 'how to build',
    'how to create', 'how to change', 'how to modify', 'how to fix',
    'how would i', 'how do i', 'plan for', 'make a plan', 'write a plan',
    'step by step', 'steps to', 'approach for', 'refactor',
)

CHAT_SYSTEM = """You are an expert on this specific codebase.
Answer questions concisely and precisely.
When asked for implementation plans, be specific: name the exact files to
change, what to add/modify, and why.
Ground every answer in the actual code you know about — no generic advice."""


async def stream_chat_response(session_id: str, message: str):
    """
    Async generator yielding text chunks.
    Builds multi-turn contents from knowledge graph + conversation history.
    """
    graph = load(session_id)
    if not graph:
        raise ValueError('Session not found')

    knowledge = to_context_string(graph)
    history   = get_history(session_id)

    # Build contents: seed turn + prior history + new user message
    contents = [
        types.Content(role='user',  parts=[types.Part.from_text(
            text=f'Here is the codebase knowledge graph:\n\n{knowledge}'
        )]),
        types.Content(role='model', parts=[types.Part.from_text(
            text='Understood. I have full context on this codebase. Ask me anything.'
        )]),
    ]

    for turn in history:
        role = 'user' if turn['role'] == 'user' else 'model'
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=turn['text'])]
        ))

    contents.append(types.Content(
        role='user',
        parts=[types.Part.from_text(text=message)]
    ))

    msg_lower = message.lower()
    model = MODEL_PLAN if any(kw in msg_lower for kw in _PLAN_KEYWORDS) else MODEL_CHAT

    config = types.GenerateContentConfig(
        system_instruction=CHAT_SYSTEM,
    )

    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=contents,
        config=config,
    ):
        if chunk.text:
            yield chunk.text

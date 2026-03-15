import asyncio
import contextvars
from google.adk.agents import Agent
from google.adk import Runner
from google.adk.sessions import InMemorySessionService

MODEL = 'gemini-2.5-flash-native-audio-preview-12-2025'

# Per-session queue so the tool can signal the WebSocket handler without
# holding a direct reference to it.  Set in run_audio_brief before tasks start.
_plan_queue:    contextvars.ContextVar[asyncio.Queue] = contextvars.ContextVar('plan_queue')
_diagram_queue: contextvars.ContextVar[asyncio.Queue] = contextvars.ContextVar('diagram_queue')


async def request_implementation_plan(description: str) -> dict:
    """Trigger the chat system to generate a detailed implementation plan.

    Call this whenever the developer asks for an implementation plan, step-by-step
    guide, or detailed how-to for any feature or change to the codebase.

    Args:
        description: The implementation task the developer wants a plan for,
                     phrased as a clear request (e.g. 'add JWT authentication').
    """
    print(f'[tool] request_implementation_plan called: {description!r}')
    q = _plan_queue.get(None)
    print(f'[tool] plan_queue from ContextVar: {q}')
    if q:
        await q.put(description)
        print(f'[tool] description enqueued')
    else:
        print(f'[tool] WARNING: no plan_queue in context — ContextVar not set')
    return {'status': 'triggered'}


async def request_architecture_diagram() -> dict:
    """Trigger the chat system to generate a visual architecture diagram of this codebase.

    Call this whenever the developer asks for an architecture diagram, visual overview,
    system diagram, or any request to see the structure of the codebase visually.
    """
    print(f'[tool] request_architecture_diagram called')
    q = _diagram_queue.get(None)
    if q:
        await q.put(True)
        print(f'[tool] diagram enqueued')
    else:
        print(f'[tool] WARNING: no diagram_queue in context — ContextVar not set')
    return {'status': 'triggered'}


BRIEFING_SYSTEM = """
You are briefing a developer on a codebase they are about to work with.
Speak like a senior engineer giving a 3-minute walkthrough to a new team member.
Cover: overall architecture, the most important files, key patterns and conventions,
and the top 3 things to know before touching anything.
Be direct. Don't pad. Pause naturally between sections.
If the developer interrupts, answer their question and continue where you left off.
When the developer asks for an implementation plan or step-by-step guide for any
feature or change, call the request_implementation_plan tool with their request and
tell them the plan is being generated in the chat window.
When the developer asks for an architecture diagram, visual overview, or to see the
system structure, call the request_architecture_diagram tool and tell them the diagram
is being generated in the chat window.
"""

agent = Agent(
    name='compass_briefer',
    model=MODEL,
    instruction=BRIEFING_SYSTEM,
    tools=[request_implementation_plan, request_architecture_diagram],
)

runner = Runner(
    app_name='compass',
    agent=agent,
    session_service=InMemorySessionService(),
)

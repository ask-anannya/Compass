from google.adk.agents import Agent
from google.adk import Runner
from google.adk.sessions import InMemorySessionService

MODEL = 'gemini-2.5-flash-native-audio-preview-12-2025'

BRIEFING_SYSTEM = """
You are briefing a developer on a codebase they are about to work with.
Speak like a senior engineer giving a 3-minute walkthrough to a new team member.
Cover: overall architecture, the most important files, key patterns and conventions,
and the top 3 things to know before touching anything.
Be direct. Don't pad. Pause naturally between sections.
If the developer interrupts, answer their question and continue where you left off.
"""

agent = Agent(
    name='compass_briefer',
    model=MODEL,
    instruction=BRIEFING_SYSTEM,
)

runner = Runner(
    app_name='compass',
    agent=agent,
    session_service=InMemorySessionService(),
)

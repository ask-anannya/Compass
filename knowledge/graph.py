import os

# In-memory store keyed by session_id
# For production: replace with Redis with a 2hr TTL
_store: dict[str, dict] = {}


def save(session_id: str, graph: dict):
    _store[session_id] = graph


def load(session_id: str) -> dict | None:
    return _store.get(session_id)


def to_context_string(graph: dict) -> str:
    """
    Flatten knowledge graph to a compact string for system prompt injection.
    Kept under ~8000 tokens so it fits comfortably alongside system instructions.
    """
    arch     = graph['architecture']
    if isinstance(arch, list):
        arch = arch[0]
    features = graph['features']
    per_file = graph['per_file']

    lines = [
        f"ARCHITECTURE: {arch['pattern']} — {arch['framework']}",
        f"LANGUAGE: {arch['language']}",
        f"ENTRY POINTS: {', '.join(arch['entry_points'])}",
        "",
        "KEY RULES:",
        *[f"  - {r}" for r in arch['rules']],
        "",
        "BEFORE YOU TOUCH ANYTHING:",
        '\n'.join(f"  - {item}" for item in arch['before_you_touch_anything'])
        if isinstance(arch['before_you_touch_anything'], list)
        else arch['before_you_touch_anything'],
        "",
        "CONVENTIONS:",
        *[f"  - {c}" for c in arch['conventions']],
        "",
        "FEATURES:",
        *[f"  - {f['feature']}: {f['description']}" for f in features],
        "",
        "FILE INDEX (path: purpose):",
        *[f"  {f['path']}: {f['purpose']}" for f in per_file]
    ]
    return '\n'.join(lines)

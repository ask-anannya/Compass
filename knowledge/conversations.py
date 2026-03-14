_store: dict[str, list] = {}


def get_history(session_id: str) -> list:
    """Return last 20 turns for this session."""
    return _store.get(session_id, [])[-40:]  # 40 entries = 20 user+model pairs


def append_turn(session_id: str, role: str, text: str):
    """role: 'user' | 'model'"""
    _store.setdefault(session_id, []).append({'role': role, 'text': text})

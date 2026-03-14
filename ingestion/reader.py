import os

SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', 'coverage', '.pytest_cache', '.mypy_cache'
}
SKIP_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
    '.woff', '.woff2', '.ttf', '.eot', '.pdf',
    '.zip', '.tar', '.gz', '.rar',
    '.lock', '.pyc', '.pyo',
    '.min.js', '.min.css',
    '.map',
    '.bin', '.so', '.dylib', '.exe'
}
MAX_FILE_BYTES = 100_000        # skip files over 100kb
TOKEN_LIMIT    = 900_000        # leave headroom for prompts within 1M window
CHARS_PER_TOKEN = 4             # rough estimate: 1 token ≈ 4 chars


def read_repo(root: str) -> list[dict]:
    """Return list of {path, content} dicts for all readable files."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTS:
                continue
            full = os.path.join(dirpath, fname)
            rel  = os.path.relpath(full, root)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                if not content:
                    continue
                files.append({'path': rel, 'content': content})
            except (OSError, PermissionError):
                continue
    return files


def estimate_tokens(files: list[dict]) -> int:
    return sum(len(f['content']) for f in files) // CHARS_PER_TOKEN


def truncate_to_limit(files: list[dict]) -> tuple[list[dict], bool]:
    """
    If files exceed TOKEN_LIMIT, drop lowest-priority files until they fit.
    Shallower files (closer to repo root) are kept — more architecturally important.
    Returns (truncated_list, was_truncated).
    """
    if estimate_tokens(files) <= TOKEN_LIMIT:
        return files, False

    sorted_files = sorted(files, key=lambda f: f['path'].count(os.sep))

    kept = []
    running = 0
    for f in sorted_files:
        tokens = len(f['content']) // CHARS_PER_TOKEN
        if running + tokens > TOKEN_LIMIT:
            continue
        kept.append(f)
        running += tokens

    return kept, True

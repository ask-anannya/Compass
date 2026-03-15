import os
import re

# ── Original skip sets (unchanged — applies to ALL repos) ─────────────────────
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
MAX_FILE_BYTES  = 100_000
TOKEN_LIMIT     = 900_000
CHARS_PER_TOKEN = 4

# ── Extra skip dirs — only applied on the large-repo path (>700k tokens) ──────
LARGE_REPO_SKIP_DIRS = {
    'test', 'tests', '__tests__', 'spec', '__spec__',
    'fixtures', 'mocks', '__mocks__',
    'examples', 'example', 'demo', 'demos',
    'docs', 'doc', 'documentation',
    'migrations', 'migration',
    'generated', 'gen', 'vendor', 'third_party',
    'assets', 'static', 'public', 'media',
    'scripts', 'tools', 'bin',
}

SKELETON_THRESHOLD = 20_000   # chars — only used on the large-repo path


def read_repo(root: str) -> tuple[list[dict], dict]:
    """
    Read all source files. Behaviour is identical to the original for all repos.
    Returns (files, stats) — stats has total, skeletonised (always 0 here), tokens.
    """
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

    stats = {
        'total':        len(files),
        'skeletonised': 0,
        'tokens':       estimate_tokens(files),
    }
    return files, stats


def reduce_for_large_repo(files: list[dict]) -> tuple[list[dict], int]:
    """
    Called ONLY when token count exceeds BATCH_TOKEN_LIMIT.
    1. Drops files whose path contains a large-repo skip directory.
    2. Skeletonises files over SKELETON_THRESHOLD chars.
    Returns (reduced_files, skeletonised_count).
    """
    reduced      = []
    skel_count   = 0

    for f in files:
        # Drop if any path component is in LARGE_REPO_SKIP_DIRS
        parts = set(f['path'].replace('\\', '/').split('/'))
        if parts & LARGE_REPO_SKIP_DIRS:
            continue

        if len(f['content']) > SKELETON_THRESHOLD:
            ext     = os.path.splitext(f['path'])[1].lower()
            content = skeletonise(f['content'], ext)
            reduced.append({'path': f['path'], 'content': content, 'skeletonised': True})
            skel_count += 1
        else:
            reduced.append(f)

    return reduced, skel_count


def skeletonise(content: str, ext: str) -> str:
    """Extract structural skeleton (signatures, imports, docstrings) from a large file."""
    lines = content.split('\n')
    kept  = []

    if ext == '.py':
        i = 0
        while i < len(lines):
            line     = lines[i]
            stripped = line.strip()
            if (stripped.startswith('def ')       or
                stripped.startswith('async def ') or
                stripped.startswith('class ')     or
                stripped.startswith('@')          or
                stripped.startswith('import ')    or
                stripped.startswith('from ')):
                kept.append(line)
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt.startswith('"""') or nxt.startswith("'''"):
                        quote = '"""' if '"""' in nxt else "'''"
                        kept.append(lines[i + 1])
                        i += 2
                        if not (nxt.count(quote) >= 2 and len(nxt) > 3):
                            while i < len(lines):
                                kept.append(lines[i])
                                if quote in lines[i]:
                                    break
                                i += 1
            i += 1

    elif ext in ('.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'):
        for line in lines:
            s = line.strip()
            if (s.startswith('function ')       or
                s.startswith('async function ') or
                s.startswith('export ')         or
                s.startswith('class ')          or
                s.startswith('const ')          or
                s.startswith('import ')         or
                s.startswith('module.exports')  or
                s.startswith('/**')             or
                s.startswith(' * ')             or
                s == '*/'):
                kept.append(line)

    elif ext == '.go':
        for line in lines:
            s = line.strip()
            if (s.startswith('func ')    or
                s.startswith('type ')    or
                s.startswith('import ')  or
                s.startswith('package ') or
                s.startswith('//')):
                kept.append(line)

    elif ext in ('.java', '.kt', '.scala'):
        for line in lines:
            s = line.strip()
            if (s.startswith('public ')    or
                s.startswith('private ')   or
                s.startswith('protected ') or
                s.startswith('class ')     or
                s.startswith('interface ') or
                s.startswith('import ')    or
                s.startswith('package ')   or
                s.startswith('//')):
                kept.append(line)

    elif ext == '.rb':
        for line in lines:
            s = line.strip()
            if (s.startswith('def ')     or
                s.startswith('class ')   or
                s.startswith('module ')  or
                s.startswith('require ') or
                s.startswith('#')):
                kept.append(line)

    else:
        kept = lines[:60]

    return '# [skeleton — full file too large]\n' + '\n'.join(kept)


def estimate_tokens(files: list[dict]) -> int:
    return sum(len(f['content']) for f in files) // CHARS_PER_TOKEN


def truncate_to_limit(files: list[dict]) -> tuple[list[dict], bool]:
    """Last-resort safety net — behaviour unchanged from original."""
    if estimate_tokens(files) <= TOKEN_LIMIT:
        return files, False

    sorted_files = sorted(files, key=lambda f: f['path'].count(os.sep))

    kept    = []
    running = 0
    for f in sorted_files:
        tokens = len(f['content']) // CHARS_PER_TOKEN
        if running + tokens > TOKEN_LIMIT:
            continue
        kept.append(f)
        running += tokens

    return kept, True

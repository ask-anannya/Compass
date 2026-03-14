import tempfile
import shutil
from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError


def clone_repo(github_url: str) -> str:
    """Clone repo to a temp directory. Returns path. Raises ValueError on bad URL."""
    tmp = tempfile.mkdtemp()
    try:
        Repo.clone_from(github_url, tmp, depth=1)  # shallow clone — saves time
    except (GitCommandError, InvalidGitRepositoryError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError(f"Could not clone repository: {e}")
    return tmp


def cleanup(path: str):
    shutil.rmtree(path, ignore_errors=True)

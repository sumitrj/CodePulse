"""One checkout per (repo, base_commit), cached and shared across arms.

Fetching a single commit shallowly is the difference between a 4-minute clone
of django and a 6-second one, and every arm reads the same tree so the
comparison is over retrieval, not over what got downloaded.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class CheckoutError(Exception):
    pass


def _git(args: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def checkout(repo: str, commit: str, cache_dir: Path) -> Path:
    """Return a path holding `repo` at `commit`. Idempotent and cached."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{repo.replace('/', '__')}__{commit[:12]}"
    stamp = target / ".bench_ready"
    if stamp.is_file():
        return target
    if target.exists():                     # a previous run died mid-fetch
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    url = f"https://github.com/{repo}.git"
    init = _git(["init", "--quiet"], cwd=target)
    if init.returncode:
        raise CheckoutError(f"git init failed for {repo}: {init.stderr.strip()}")
    _git(["remote", "add", "origin", url], cwd=target)
    # GitHub allows fetching a bare sha, which skips the rest of history.
    fetch = _git(["fetch", "--depth", "1", "--quiet", "origin", commit], cwd=target)
    if fetch.returncode:
        # Some mirrors refuse sha fetches; fall back to a full but blobless clone.
        shutil.rmtree(target, ignore_errors=True)
        clone = _git(["clone", "--filter=blob:none", "--no-checkout", "--quiet",
                      url, str(target)], cwd=cache_dir)
        if clone.returncode:
            raise CheckoutError(f"cannot fetch {repo}: {clone.stderr.strip()}")
    out = _git(["checkout", "--quiet", "--force", commit], cwd=target)
    if out.returncode:
        raise CheckoutError(f"cannot check out {repo}@{commit[:12]}: {out.stderr.strip()}")
    stamp.write_text(f"{repo} {commit}\n")
    return target


def discard(repo: str, commit: str, cache_dir: Path) -> None:
    """Drop one checkout. A 500-instance run over ~100 MB working trees needs
    ~50 GB if everything is kept; streaming keeps peak disk at roughly one
    repo, at the cost of re-cloning on a later run."""
    target = Path(cache_dir) / f"{repo.replace('/', '__')}__{commit[:12]}"
    shutil.rmtree(target, ignore_errors=True)


def clear(cache_dir: Path) -> None:
    shutil.rmtree(Path(cache_dir), ignore_errors=True)

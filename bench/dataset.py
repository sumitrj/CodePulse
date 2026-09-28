"""SWE-bench Lite, fetched and cached with nothing but the standard library.

The dataset is 300 Python instances. We pull it over the HuggingFace
datasets-server rows API rather than `datasets`, because pulling in pyarrow to
read 300 rows would double this project's dependency footprint for no gain.
See specs/bench/SPEC.md.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

SWEBENCH_LITE = "princeton-nlp/SWE-bench_Lite"          # 300, the development set
SWEBENCH_VERIFIED = "princeton-nlp/SWE-bench_Verified"  # 500, human-validated
SWEBENCH_FULL = "princeton-nlp/SWE-bench"

DATASETS = {
    "lite": SWEBENCH_LITE,
    "verified": SWEBENCH_VERIFIED,
    "full": SWEBENCH_FULL,
}


def resolve(name: str) -> str:
    """Accept a short alias or a full HuggingFace dataset id."""
    return DATASETS.get(name.strip().lower(), name.strip())


_ROWS_API = "https://datasets-server.huggingface.co/rows"
_PAGE = 100          # the rows API caps a page at 100
_TIMEOUT = 60


@dataclass(frozen=True)
class Instance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str = ""          # the gold fix: oracle input only, never an arm's

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    def redacted(self) -> "Instance":
        """The copy an arm is allowed to see. AC1: the fix is not an input."""
        return replace(self, patch="")


def _fetch_page(dataset: str, offset: int, length: int) -> dict:
    query = urllib.parse.urlencode({
        "dataset": dataset, "config": "default", "split": "test",
        "offset": offset, "length": length,
    })
    with urllib.request.urlopen(f"{_ROWS_API}?{query}", timeout=_TIMEOUT) as response:
        return json.loads(response.read())


def download(cache_dir: Path, dataset: str = SWEBENCH_LITE) -> Path:
    """Cache the split as JSONL. Returns the cache path; re-download is a no-op."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{dataset.replace('/', '__')}.test.jsonl"
    if target.is_file() and target.stat().st_size:
        return target
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        page = _fetch_page(dataset, offset, _PAGE)
        total = page["num_rows_total"] if total is None else total
        batch = page.get("rows", [])
        if not batch:                       # defensive: never spin on an empty page
            break
        rows.extend(item["row"] for item in batch)
        offset += len(batch)
    # Write through a temp file so an interrupted download can't leave a
    # half-written cache that later runs would trust.
    scratch = target.with_suffix(".partial")
    with scratch.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    scratch.replace(target)
    return target


def load(cache_dir: Path, limit: int | None = None, repos: Sequence[str] = (),
         instances: Sequence[str] = (), dataset: str = SWEBENCH_LITE) -> list[Instance]:
    """Load the split, optionally filtered. `limit` samples deterministically."""
    path = download(cache_dir, dataset)
    pool: list[Instance] = []
    wanted_repos = {r.lower() for r in repos}
    wanted_ids = set(instances)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if wanted_repos and row["repo"].lower() not in wanted_repos:
            continue
        if wanted_ids and row["instance_id"] not in wanted_ids:
            continue
        pool.append(Instance(
            instance_id=row["instance_id"],
            repo=row["repo"],
            base_commit=row["base_commit"],
            problem_statement=row["problem_statement"],
            patch=row["patch"],
        ))
    pool.sort(key=lambda i: i.instance_id)
    return sample(pool, limit) if limit else pool


def sample(pool: list[Instance], limit: int) -> list[Instance]:
    """Repo-stratified, deterministic (AC7).

    Taking the first N by id would spend a 30-instance budget entirely on
    astropy and django. Round-robin over repos instead, so a small run still
    says something about every codebase in the set. No RNG: the order is a
    pure function of the pool.
    """
    if limit >= len(pool):
        return list(pool)
    by_repo: dict[str, list[Instance]] = defaultdict(list)
    for instance in sorted(pool, key=lambda i: i.instance_id):
        by_repo[instance.repo].append(instance)
    picked: list[Instance] = []
    depth = 0
    while len(picked) < limit:
        progressed = False
        for repo in sorted(by_repo):
            bucket = by_repo[repo]
            if depth < len(bucket):
                picked.append(bucket[depth])
                progressed = True
                if len(picked) == limit:
                    break
        if not progressed:                  # pool exhausted before the limit
            break
        depth += 1
    return sorted(picked, key=lambda i: i.instance_id)

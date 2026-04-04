import hashlib
import json

from memory.models import Commit


def compute_sha(commit_data: dict) -> str:
    """
    Deterministic SHA-256 over the commit dict (excluding the sha field itself).
    12-char hex prefix — collision-safe for this scale.
    """
    data = {k: v for k, v in commit_data.items() if k != "sha"}
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def sha_for_commit(commit: Commit) -> str:
    return compute_sha(commit.model_dump())

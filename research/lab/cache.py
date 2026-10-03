"""On-disk JSON cache for downloaded market data so sweeps never re-download (dir override: RESEARCH_CACHE_DIR)."""
import hashlib
import json
import os
from typing import Any, Optional

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")


def cache_dir() -> str:
    d = os.environ.get("RESEARCH_CACHE_DIR", DEFAULT_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _path(key: dict) -> str:
    h = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]
    return os.path.join(cache_dir(), f"{key.get('kind', 'x')}_{key.get('venue', 'v')}_{h}.json")


def get(key: dict) -> Optional[Any]:
    p = _path(key)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def put(key: dict, value: Any) -> None:
    p = _path(key)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, p)

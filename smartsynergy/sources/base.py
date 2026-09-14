"""Shared HTTP plumbing: on-disk cache, polite rate limiting, bounded retries."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from smartsynergy.schemas import Coverage, SourceRecord


class SourceError(RuntimeError):
    """Retrieval failed in a way the caller must report as coverage='failed'."""


@dataclass
class SearchResult:
    """Always carries coverage: an empty list from a failure is not an empty list from a clean search."""

    records: list[SourceRecord] = field(default_factory=list)
    coverage: Coverage = "complete"
    next_cursor: str | None = None
    total_hits: int = 0
    note: str = ""


class HttpClient:
    """Cached, rate-limited, retrying HTTP GET returning JSON or text."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | str = ".cache/http",
        min_interval_s: float = 0.25,
        timeout_s: float = 30.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_s,
            follow_redirects=True,
        )

    def _cache_path(self, url: str, params: dict[str, Any]) -> Path:
        key = hashlib.sha256(f"{url}?{json.dumps(params, sort_keys=True)}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last_call = time.monotonic()

    def get_json(
        self, url: str, params: dict[str, Any] | None = None, *, use_cache: bool = True, retries: int = 3
    ) -> dict[str, Any]:
        params = params or {}
        cache_file = self._cache_path(url, params)
        if use_cache and cache_file.exists():
            return json.loads(cache_file.read_text())

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                self._throttle()
                resp = self._client.get(url, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                cache_file.write_text(json.dumps(data))
                return data
            except Exception as exc:  # noqa: BLE001 - classified by the caller as coverage
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(2**attempt)
        raise SourceError(f"GET {url} failed after {retries} attempts: {last_exc}")

    def close(self) -> None:
        self._client.close()

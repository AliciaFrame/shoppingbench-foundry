from __future__ import annotations

import os
import random
import time
from functools import lru_cache
from threading import BoundedSemaphore, Lock
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI, RateLimitError

_MAX_CONCURRENCY = int(os.getenv("WEB_SEARCH_MAX_CONCURRENCY", "16"))
_MAX_RETRIES = int(os.getenv("WEB_SEARCH_MAX_RETRIES", "8"))
_MAX_OUTPUT_TOKENS = int(os.getenv("WEB_SEARCH_MAX_OUTPUT_TOKENS", "256"))
_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("WEB_SEARCH_MIN_REQUEST_INTERVAL_SECONDS", "0.25")
)
_WEB_SEARCH_SLOTS = BoundedSemaphore(_MAX_CONCURRENCY)
_WEB_SEARCH_RATE_LOCK = Lock()
_next_request_at = 0.0


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _credential_scope(endpoint: str) -> str:
    if ".openai.azure.com" in endpoint:
        return "https://cognitiveservices.azure.com/.default"
    return "https://ai.azure.com/.default"


@lru_cache
def get_web_search_client() -> OpenAI:
    base_url = _required_env("WEB_SEARCH_OPENAI_BASE_URL").rstrip("/") + "/"
    api_key: str | Any = os.getenv("WEB_SEARCH_API_KEY", "").strip()
    if not api_key:
        api_key = get_bearer_token_provider(
            DefaultAzureCredential(),
            _credential_scope(base_url),
        )
    return OpenAI(base_url=base_url, api_key=api_key)


def _web_sources(response: Any) -> list[dict[str, str]]:
    payload = response.model_dump(mode="json")
    sources: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            citation = value.get("url_citation")
            if isinstance(citation, dict) and citation.get("url"):
                source = {
                    "title": str(citation.get("title", "")),
                    "url": str(citation["url"]),
                }
                if source not in sources:
                    sources.append(source)
            if value.get("type") == "url_citation" and value.get("url"):
                source = {
                    "title": str(value.get("title", "")),
                    "url": str(value["url"]),
                }
                if source not in sources:
                    sources.append(source)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return sources


def _retry_delay(error: RateLimitError, attempt: int) -> float:
    headers = error.response.headers
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms:
        return max(0.0, float(retry_after_ms) / 1000)
    retry_after = headers.get("retry-after")
    if retry_after:
        return max(0.0, float(retry_after))
    return min(60.0, 2**attempt) + random.uniform(0.0, 0.5)


def _wait_for_request_slot() -> None:
    global _next_request_at
    with _WEB_SEARCH_RATE_LOCK:
        now = time.monotonic()
        delay = max(0.0, _next_request_at - now)
        if delay:
            time.sleep(delay)
            now = time.monotonic()
        _next_request_at = max(now, _next_request_at) + _MIN_REQUEST_INTERVAL_SECONDS


@lru_cache(maxsize=512)
def search_web(query: str) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("Web search query cannot be empty")
    with _WEB_SEARCH_SLOTS:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                _wait_for_request_slot()
                response = get_web_search_client().responses.create(
                    model=_required_env("WEB_SEARCH_MODEL_DEPLOYMENT_NAME"),
                    instructions=(
                        "Use web search to answer the factual clue precisely. Return only the "
                        "short grounded answer needed for the shopping task, with citations."
                    ),
                    input=query,
                    tools=[{"type": "web_search"}],
                    max_output_tokens=_MAX_OUTPUT_TOKENS,
                )
            except RateLimitError as exc:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(_retry_delay(exc, attempt))
                continue
            answer = (response.output_text or "").strip()
            if answer:
                return {"answer": answer, "sources": _web_sources(response)}
            if attempt == _MAX_RETRIES:
                raise RuntimeError("Web search returned no grounded answer")
            time.sleep(min(10.0, 2**attempt) + random.uniform(0.0, 0.5))
    raise RuntimeError("Web search retry loop exited unexpectedly")

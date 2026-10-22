from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel, Field

from .rft_grading import endpoint_grade
from .store import INFORMATION_FIELDS, ProductStore, create_store
from .web_rft_grading_v5 import endpoint_grade as web_v5_endpoint_grade
from .web_search import _retry_delay, search_web
from .web_search_rft_grading_v6 import endpoint_grade as web_search_v6_endpoint_grade

app = FastAPI(title="ShoppingBench Foundry Runtime", version="0.1.0")


class ToolRequest(BaseModel):
    call_id: str
    id: str | None = None
    name: str | None = None
    arguments: dict[str, Any] | str = Field(default_factory=dict)
    trace_id: str | None = None
    item: dict[str, Any] = Field(default_factory=dict)

    def parsed_arguments(self) -> dict[str, Any]:
        if isinstance(self.arguments, dict):
            return self.arguments
        try:
            parsed = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("Tool arguments must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise TypeError("Tool arguments must decode to a JSON object")
        return parsed


class GradeRequest(BaseModel):
    sample: dict[str, Any]
    item: dict[str, Any]
    trace_id: str | None = None


@lru_cache
def get_store() -> ProductStore:
    return create_store()


StoreDependency = Annotated[ProductStore, Depends(get_store)]
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(
        HTTPBearer(
            auto_error=False,
            description="Paste the ShoppingBench API token. Swagger adds the Bearer prefix.",
        )
    ),
]


def require_token(credentials: BearerCredentials) -> None:
    expected = os.getenv("SHOPPINGBENCH_API_TOKEN")
    if not expected:
        if os.getenv("SHOPPINGBENCH_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"}:
            return
        raise HTTPException(status_code=503, detail="Bearer authentication is not configured")
    supplied = (
        credentials.credentials
        if credentials is not None and credentials.scheme.lower() == "bearer"
        else ""
    )
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/find_product", dependencies=[Depends(require_token)])
def find_product(
    q: str,
    page: int,
    store: StoreDependency,
    shop_id: str | None = None,
    price: str | None = None,
    sort: str | None = None,
    service: str | None = None,
) -> list[dict[str, Any]]:
    return store.search(q, page, shop_id, price, sort, service)


@app.get("/view_product_information", dependencies=[Depends(require_token)])
def view_product_information(
    product_ids: str,
    store: StoreDependency,
) -> list[dict[str, Any]]:
    products = store.get_products([item.strip() for item in product_ids.split(",") if item.strip()])
    return [{field: product.get(field) for field in INFORMATION_FIELDS} for product in products]


@app.post("/rft/tools/{tool_name}", dependencies=[Depends(require_token)])
def rft_tool(
    tool_name: str,
    request: ToolRequest,
    store: StoreDependency,
) -> dict[str, str]:
    try:
        arguments = request.parsed_arguments()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.name and request.name != tool_name:
        raise HTTPException(status_code=400, detail="Tool name does not match route")

    if tool_name == "web_search":
        output = search_web(str(arguments["query"]))
    elif tool_name == "find_product":
        output: Any = store.search(
            arguments["q"],
            arguments["page"],
            arguments.get("shop_id"),
            arguments.get("price"),
            arguments.get("sort"),
            arguments.get("service"),
        )
    elif tool_name == "view_product_information":
        ids = [item.strip() for item in arguments.get("product_ids", "").split(",") if item.strip()]
        products = store.get_products(ids)
        output = [
            {field: product.get(field) for field in INFORMATION_FIELDS} for product in products
        ]
    elif tool_name == "recommend_product":
        output = {"recommended": arguments.get("product_ids", "")}
    elif tool_name == "terminate":
        output = {"terminated": True, **arguments}
    else:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    return {
        "type": "function_call_output",
        "call_id": request.call_id,
        "output": json.dumps(output, ensure_ascii=False),
        "id": request.id or f"fc_{request.call_id}",
    }


@app.post("/grade/v3", dependencies=[Depends(require_token)])
def grade_v3(
    request: GradeRequest,
) -> dict[str, float]:
    return endpoint_grade(request.model_dump())


@app.post("/grade/web/v5", dependencies=[Depends(require_token)])
def grade_web_v5(
    request: GradeRequest,
) -> dict[str, float]:
    return web_v5_endpoint_grade(request.model_dump())


def _grade_with_judge_retries(
    grade: Callable[[dict[str, Any]], dict[str, float]],
    payload: dict[str, Any],
) -> dict[str, float]:
    # Retries live here, outside the fingerprinted grader modules, so transport
    # handling cannot change calibrated scoring. Only unreachable or throttled
    # judge requests are retried; a judge answer is never resampled.
    attempts = max(1, int(os.getenv("RFT_GRADER_TRANSPORT_ATTEMPTS", "4")))
    backoff = float(os.getenv("RFT_GRADER_TRANSPORT_BACKOFF_SECONDS", "2"))
    for attempt in range(attempts):
        try:
            return grade(payload)
        except APIConnectionError as exc:
            if attempt == attempts - 1:
                raise HTTPException(
                    status_code=503,
                    detail="Grading judge endpoint is unreachable",
                ) from exc
            time.sleep(backoff * 2**attempt)
        except RateLimitError as exc:
            if attempt == attempts - 1:
                raise HTTPException(
                    status_code=503,
                    detail="Grading judge is rate limited",
                ) from exc
            time.sleep(min(20.0, max(backoff, _retry_delay(exc, attempt))))
    raise AssertionError("unreachable")


@app.post("/grade/web-search/v6", dependencies=[Depends(require_token)])
def grade_web_search_v6(request: GradeRequest) -> dict[str, float]:
    return _grade_with_judge_retries(web_search_v6_endpoint_grade, request.model_dump())

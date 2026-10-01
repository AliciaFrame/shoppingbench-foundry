from __future__ import annotations

import json
import os
import secrets
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .rft_grading import endpoint_grade
from .store import INFORMATION_FIELDS, ProductStore, create_store

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


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("SHOPPINGBENCH_API_TOKEN")
    if not expected:
        if os.getenv("SHOPPINGBENCH_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"}:
            return
        raise HTTPException(status_code=503, detail="Bearer authentication is not configured")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
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

    if tool_name == "find_product":
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
        output = {"terminated": True}
    else:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    return {
        "type": "function_call_output",
        "call_id": request.call_id,
        "output": json.dumps(output, ensure_ascii=False),
        "id": request.id or f"fc_{request.call_id}",
    }


@app.post("/grade", dependencies=[Depends(require_token)])
def grade(
    request: GradeRequest,
) -> dict[str, float]:
    return endpoint_grade(request.model_dump())

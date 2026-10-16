"""Authenticated endpoint grader and agentic-RFT tool adapter."""

import asyncio
import hmac
import json
import logging
import math
import os

from domain import execute_tool
from fastapi import FastAPI, Header, HTTPException, Request
from grader import grade

app = FastAPI()
logger = logging.getLogger("hillclimb.endpoints")
MAX_BYTES = 1_000_000


async def payload(request, authorization):
    token = os.environ["HILLCLIMB_ENDPOINT_TOKEN"]
    if not token:
        raise RuntimeError("HILLCLIMB_ENDPOINT_TOKEN cannot be empty")
    if not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "Unauthorized")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(413, "Payload exceeds 1 MB")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(400, "Invalid JSON") from error
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object")
    return value


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/grade")
async def endpoint_grade(request: Request, authorization: str | None = Header(default=None)):
    body = await payload(request, authorization)
    try:
        reward = await asyncio.to_thread(grade, body["sample"], body["item"])
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(reward)
            or not 0 <= reward <= 1
        ):
            raise ValueError("Reward must be finite and within [0,1]")
    except (KeyError, TypeError, ValueError) as error:
        logger.warning("Invalid grading request: %s", error)
        raise HTTPException(422, str(error)) from error
    return {"score": reward}


async def tool_response(request, authorization, tool_name):
    body = await payload(request, authorization)
    try:
        arguments = body["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise TypeError("Tool arguments must be an object")
        if tool_name and body.get("name") not in (None, tool_name):
            raise HTTPException(400, "Tool name does not match route")
        result = await asyncio.to_thread(execute_tool, tool_name or body["name"], arguments)
        response = {
            "type": "function_call_output",
            "call_id": body["call_id"],
            "id": body.get("id") or f"fc_{body['call_id']}",
            "output": json.dumps(result),
        }
        if len(json.dumps(response).encode("utf-8")) > MAX_BYTES:
            raise HTTPException(413, "Tool output exceeds 1 MB")
        return response
    except (KeyError, TypeError, ValueError) as error:
        logger.warning("Invalid tool request: %s", error)
        raise HTTPException(422, str(error)) from error


@app.post("/tools")
async def endpoint_tool(request: Request, authorization: str | None = Header(default=None)):
    return await tool_response(request, authorization, None)


@app.post("/tools/{tool_name}")
async def endpoint_named_tool(
    tool_name: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    return await tool_response(request, authorization, tool_name)

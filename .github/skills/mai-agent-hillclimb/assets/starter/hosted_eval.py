"""Evaluate an exact hosted version in an isolated session per episode."""

import json
import os
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import VersionRefIndicator
from azure.identity import DefaultAzureCredential
from evaluation_auth import evaluation_key, evaluation_metadata
from workload import decode_trace, encode_input


class HostedEpisodeFailure(RuntimeError):
    def __init__(self, calls):
        super().__init__("Hosted agent generation failed")
        self.calls = calls


def failure_diagnostics(project, name, version, session_id):
    # Opaque console logs and error messages can contain credentials even without
    # recognizable secret keys. Export only allowlisted state and log categories.
    diagnostics = {}
    try:
        session = project.agents.get_session(
            agent_name=name,
            session_id=session_id,
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
        )
        status = session.status
    except Exception:  # noqa: BLE001 -- Diagnostics must not replace the episode failure.
        diagnostics["session_diagnostics"] = "unavailable"
    else:
        diagnostics["session_status"] = (
            status
            if status in ("active", "failed", "stopped", "starting", "stopping", "pending")
            else "unknown"
        )
    try:
        diagnostics["logs"] = project.agents.get_session_log_stream(
            agent_name=name,
            agent_version=version,
            session_id=session_id,
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
            cls=summarize_logs,
        )
    except Exception:  # noqa: BLE001 -- Diagnostics must not replace the episode failure.
        diagnostics["logs"] = {"capture": "unavailable"}
    return diagnostics


def summarize_logs(pipeline_response, chunks, _headers):
    deadline = time.monotonic() + 5
    observed = bytearray()
    try:
        for index, chunk in enumerate(chunks):
            observed.extend(chunk[: 8192 - len(observed)])
            if len(observed) >= 8192 or index >= 15 or time.monotonic() >= deadline:
                break
    finally:
        pipeline_response.http_response.close()
    text = observed.decode("utf-8", errors="replace").lower()
    return {
        "bytes_observed": len(observed),
        "signals": [
            signal
            for signal in (
                "timeout",
                "incomplete",
                "credential",
                "unauthorized",
                "forbidden",
                "rate_limit",
                "traceback",
                "connection",
            )
            if signal in text
        ],
    }


def failed_calls(response):
    error = getattr(response, "error", None)
    try:
        trace = json.loads(error.message) if error else {}
    except (TypeError, ValueError):
        return []
    if (
        isinstance(trace, dict)
        and trace.get("hillclimb_trace_version") == 1
        and trace.get("status") == "failed"
        and isinstance(trace.get("calls"), list)
        and all(isinstance(call, dict) for call in trace["calls"])
    ):
        return trace["calls"]
    return []


def run_episode(item):
    name = os.environ["MAI_HOSTED_AGENT_NAME"]
    version = os.environ["MAI_HOSTED_AGENT_VERSION"]
    inputs = encode_input(item)
    evaluation_key()
    with (
        DefaultAzureCredential(process_timeout=60) as credential,
        AIProjectClient(
            endpoint=os.environ["MAI_PROJECT_ENDPOINT"],
            credential=credential,
            allow_preview=True,
            retry_total=0,
        ) as project,
    ):
        resource = project.agents.get_version(agent_name=name, agent_version=version)
        if resource.status != "active":
            raise RuntimeError(f"Hosted version {name}/{version} is not active: {resource.status}")
        environment = getattr(resource.definition, "environment_variables", {}) or {}
        if environment.get("HILLCLIMB_ENABLE_EVALUATION") != "1":
            raise PermissionError("Hosted evaluation is not enabled on this exact agent version")
        session = project.agents.create_session(
            agent_name=name, version_indicator=VersionRefIndicator(agent_version=version)
        )
        original_error = None
        try:
            deadline = time.monotonic() + 300
            while session.status != "active":
                if session.status == "failed" or time.monotonic() >= deadline:
                    raise RuntimeError(f"Hosted session not active: {session.status}")
                time.sleep(5)
                session = project.agents.get_session(
                    agent_name=name, session_id=session.agent_session_id
                )
            with project.get_openai_client(agent_name=name, max_retries=0, timeout=300) as api:
                response = api.responses.create(
                    input=inputs,
                    metadata=evaluation_metadata(inputs),
                    extra_body={"agent_session_id": session.agent_session_id},
                )
            if getattr(response, "status", None) not in (None, "completed") or getattr(
                response, "incomplete_details", None
            ):
                raise HostedEpisodeFailure(failed_calls(response))
            if not response.output_text:
                raise RuntimeError("Hosted agent returned an empty answer")
            result = decode_trace(response.output_text)
            return {
                **result,
                "hosted_agent": {
                    "name": name,
                    "version": version,
                    "session_id": session.agent_session_id,
                },
            }
        except Exception as error:
            original_error = error
            try:
                diagnostics = failure_diagnostics(project, name, version, session.agent_session_id)
            except Exception:  # noqa: BLE001 -- Preserve the original failure unconditionally.
                diagnostics = {"session_diagnostics": "unavailable"}
            error.hosted_diagnostics = diagnostics
            error.add_note("Hosted session diagnostics: " + json.dumps(diagnostics))
            raise
        finally:
            try:
                project.agents.stop_session(agent_name=name, session_id=session.agent_session_id)
            except Exception:
                if original_error is None:
                    raise
                original_error.add_note("Hosted session cleanup failed")

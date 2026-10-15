"""Evaluate an exact hosted version in an isolated session per episode."""

import os
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import VersionRefIndicator
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from evaluation_auth import evaluation_key, evaluation_metadata
from openai import APIError
from workload import decode_trace, encode_input


def run_episode(item):
    name = os.environ["MAI_HOSTED_AGENT_NAME"]
    version = os.environ["MAI_HOSTED_AGENT_VERSION"]
    inputs = encode_input(item)
    evaluation_key()
    with AIProjectClient(
        endpoint=os.environ["MAI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
        allow_preview=True,
        retry_total=0,
    ) as project:
        resource = project.agents.get_version(agent_name=name, agent_version=version)
        if resource.status != "active":
            raise RuntimeError(f"Hosted version {name}/{version} is not active: {resource.status}")
        environment = getattr(resource.definition, "environment_variables", {}) or {}
        if environment.get("HILLCLIMB_ENABLE_EVALUATION") != "1":
            raise PermissionError("Hosted evaluation is not enabled on this exact agent version")
        session = project.agents.create_session(
            agent_name=name, version_indicator=VersionRefIndicator(agent_version=version)
        )
        deadline = time.monotonic() + 300
        try:
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
        except (APIError, HttpResponseError, RuntimeError) as error:
            error.add_note(f"Hosted agent {name}/{version}, session {session.agent_session_id}")
            raise
        finally:
            project.agents.stop_session(agent_name=name, session_id=session.agent_session_id)

"""Evaluate an exact hosted version in an isolated session per episode."""

import os
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import VersionRefIndicator
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from openai import APIError


def run_episode(item):
    name = os.environ["MAI_HOSTED_AGENT_NAME"]
    version = os.environ["MAI_HOSTED_AGENT_VERSION"]
    with AIProjectClient(
        endpoint=os.environ["MAI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
        allow_preview=True,
        retry_total=0,
    ) as project:
        resource = project.agents.get_version(agent_name=name, agent_version=version)
        if resource.status != "active":
            raise RuntimeError(f"Hosted version {name}/{version} is not active: {resource.status}")
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
                    input=item["messages"],
                    extra_body={"agent_session_id": session.agent_session_id},
                )
            if not response.output_text:
                raise RuntimeError("Hosted agent returned an empty answer")
            return {
                "output_text": response.output_text,
                "output_tools": [],
                "calls": [
                    {
                        "model": os.environ["MAI_MODEL_DEPLOYMENT_NAME"],
                        "purpose": "inference",
                        "aggregated": True,
                        "cache_write_mode": os.getenv("MAI_CACHE_WRITE_MODE", "none"),
                        "raw_usage": response.usage.model_dump() if response.usage else None,
                    }
                ],
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

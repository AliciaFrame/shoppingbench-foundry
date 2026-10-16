"""Plain hosted smoke and isolated SDK application of optimizer configurations."""

import copy
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from journey import write_json

MAX_CODE_BYTES = 250 * 1024 * 1024


def smoke(api, config, text, state, receipt, timeout):
    from azure.ai.projects.models import VersionRefIndicator
    from azure.core.exceptions import AzureError

    if timeout <= 0:
        raise ValueError("Smoke timeout must be positive")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Smoke input must be nonempty text")
    name, version = config["agent_name"], config["agent_version"]
    resource = api.agents.get_version(agent_name=name, agent_version=version)
    if resource.status != "active":
        raise RuntimeError(f"Hosted version is not active: {resource.status}")
    session = api.agents.create_session(
        agent_name=name, version_indicator=VersionRefIndicator(agent_version=version)
    )
    state.update(session_id=session.agent_session_id, status="invoking")
    write_json(receipt, state)
    started = time.monotonic()
    try:
        deadline = started + timeout
        while session.status != "active":
            if session.status == "failed" or time.monotonic() >= deadline:
                raise RuntimeError(f"Smoke session is not active: {session.status}")
            time.sleep(5)
            session = api.agents.get_session(agent_name=name, session_id=session.agent_session_id)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Smoke timed out before invocation")
        with api.get_openai_client(agent_name=name, max_retries=0, timeout=remaining) as client:
            response = client.responses.create(
                input=text, extra_body={"agent_session_id": session.agent_session_id}
            )
        incomplete = getattr(response, "incomplete_details", None)
        state.update(
            returned_model=getattr(response, "model", None),
            response_status=getattr(response, "status", None),
            incomplete_details=incomplete.model_dump() if incomplete else None,
            usage=response.usage.model_dump() if response.usage else None,
        )
        write_json(receipt, state)
        if (
            getattr(response, "status", None) not in (None, "completed")
            or incomplete
            or not response.output_text
        ):
            raise RuntimeError("Hosted smoke returned an incomplete or empty response")
        state.update(
            status="completed",
            response_id=response.id,
            output_text=response.output_text,
            latency_seconds=time.monotonic() - started,
        )
        write_json(receipt, state)
        return state
    finally:
        original_error = sys.exception()
        try:
            api.agents.stop_session(agent_name=name, session_id=session.agent_session_id)
        except (AzureError, RuntimeError) as cleanup_error:
            state["cleanup_error"] = {"type": type(cleanup_error).__name__}
            write_json(receipt, state)
            if original_error is None:
                raise
            original_error.add_note("Session cleanup also failed; see the smoke receipt")


def member_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Candidate metadata paths must be nonempty strings")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in str(path):
        raise ValueError("Candidate metadata paths must remain inside the code archive")
    return str(path)


def candidate_archive(original, profile):
    """Materialize every supported mutation without changing runtime code."""
    import yaml

    with zipfile.ZipFile(io.BytesIO(original)) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate code archive members")
        for name in names:
            member_path(name)
        if sum(info.file_size for info in source.infolist()) > MAX_CODE_BYTES:
            raise ValueError("Expanded code archive exceeds the application limit")
        members = {info.filename: copy.copy(info) for info in source.infolist()}
        if "metadata.yaml" not in names:
            raise ValueError("SDK application requires the skill starter's root metadata.yaml")
        files = {name: source.read(name) for name in names}
    metadata = yaml.safe_load(files["metadata.yaml"].decode("utf-8"))
    if not isinstance(metadata, dict):
        raise TypeError("Invalid baseline optimization metadata")
    if not isinstance(profile.get("instructions"), str) or not profile["instructions"].strip():
        raise ValueError("Candidate instructions are missing")
    if not isinstance(profile.get("model"), str) or not profile["model"].strip():
        raise ValueError("Candidate model is missing")
    tools, skills = profile.get("tool_definitions"), profile.get("skills")
    tools = [] if tools is None else tools
    skills = [] if skills is None else skills
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise ValueError("Candidate tools must be a list of definitions")
    if not isinstance(skills, list):
        raise TypeError("Candidate skills must be a list")
    instructions = member_path(metadata.get("instruction_file", "instructions.md"))
    tool_file = member_path(metadata.get("tools_file", "tools.json"))
    if len({"metadata.yaml", instructions, tool_file}) != 3:
        raise ValueError("Optimization metadata files must be distinct")
    files[instructions] = profile["instructions"].encode("utf-8")
    files[tool_file] = json.dumps(tools, indent=2).encode("utf-8")
    metadata.update(instruction_file=instructions, tools_file=tool_file, model=profile["model"])
    folder = f"hillclimb_candidate_skills_{hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest()[:12]}"
    metadata["skill_dir"] = folder
    seen = set()
    for index, skill in enumerate(skills):
        if (
            not isinstance(skill, dict)
            or not all(isinstance(skill.get(key), str) for key in ("name", "description", "body"))
            or not skill["name"].strip()
            or skill["name"] in seen
        ):
            raise ValueError("Candidate skills need unique names, descriptions and bodies")
        seen.add(skill["name"])
        header = yaml.safe_dump(
            {"name": skill["name"], "description": skill["description"]}, sort_keys=False
        )
        name = f"{folder}/skill-{index:03d}/SKILL.md"
        content = f"---\n{header}---\n{skill['body']}\n".encode()
        if name in files and files[name] != content:
            raise ValueError("Candidate skill path collides with existing code")
        files[name] = content
    files["metadata.yaml"] = yaml.safe_dump(metadata, sort_keys=False).encode("utf-8")
    # The SDK looks for <config_dir>/baseline, while the starter also has a
    # root-file fallback. Materialize both supported default discovery paths.
    for prefix in ("baseline", ".agent_configs/baseline"):
        sdk_metadata = yaml.safe_load(
            files.get(f"{prefix}/metadata.yaml", files["metadata.yaml"]).decode("utf-8")
        )
        if not isinstance(sdk_metadata, dict):
            raise TypeError("Invalid SDK baseline metadata")
        sdk_metadata.update(
            instruction_file="instructions.md",
            tool_file="tools.json",
            skill_dir=folder,
            model=profile["model"],
        )
        files[f"{prefix}/instructions.md"] = profile["instructions"].encode("utf-8")
        files[f"{prefix}/tools.json"] = json.dumps(tools, indent=2).encode("utf-8")
        for name, content in list(files.items()):
            if name.startswith(folder + "/"):
                files[f"{prefix}/{name}"] = content
        files[f"{prefix}/metadata.yaml"] = yaml.safe_dump(sdk_metadata, sort_keys=False).encode(
            "utf-8"
        )
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(members.get(name, name), content)
    return result.getvalue()


def deploy(api, config, optimization, profile, candidate_id, state, receipt, timeout):
    from azure.ai.projects.models import (
        AgentEndpointConfig,
        CodeConfiguration,
        FixedRatioVersionSelectionRule,
        VersionSelector,
    )

    if timeout <= 0:
        raise ValueError("Deployment timeout must be positive")
    target = optimization["job"]["target_configuration"]
    name, baseline_version = config["agent_name"], config["agent_version"]
    if target.get("name") != name or target.get("version") != baseline_version:
        raise ValueError("Candidate target must match the immutable baseline name and version")
    baseline = api.agents.get_version(agent_name=name, agent_version=baseline_version)
    if baseline.status != "active":
        raise ValueError("Baseline version is not active")
    if not getattr(baseline.definition, "code_configuration", None):
        raise ValueError("SDK candidate application requires a code-based hosted agent")
    override = baseline.definition.environment_variables.get("MAI_MODEL_OVERRIDE")
    if override and override != profile["model"]:
        raise ValueError("A baseline model override would hide the candidate's selected model")
    if baseline.definition.environment_variables.get("OPTIMIZATION_LOCAL_DIR"):
        raise ValueError("SDK application requires the starter's default config directory")
    original = bytearray()
    for chunk in api.agents.download_code(agent_name=name, agent_version=baseline_version):
        original.extend(chunk)
        if len(original) > MAX_CODE_BYTES:
            raise ValueError("Baseline code archive exceeds the SDK upload limit")
    original = bytes(original)
    if hashlib.sha256(original).hexdigest() != baseline.definition.code_configuration.content_hash:
        raise ValueError("Downloaded baseline archive fingerprint does not match its version")
    code = candidate_archive(original, profile)
    if len(code) > MAX_CODE_BYTES:
        raise ValueError("Candidate archive exceeds the SDK upload limit")
    archive = Path(receipt).with_name(Path(receipt).stem + "-code.zip")
    with archive.open("xb") as handle:
        handle.write(code)
    details = api.agents.get(agent_name=name)
    previous = details.agent_endpoint.version_selector.as_dict()
    rules = previous.get("version_selection_rules")
    if not rules:
        raise ValueError("Cannot determine ordinary serving routes; isolate the target manually")
    frozen = []
    for rule in rules:
        version = rule["agent_version"]
        if version == "@latest":
            version = details.versions.latest.version
        elif version.startswith("@"):
            raise ValueError("Cannot safely freeze an unknown serving-version alias")
        if rule["type"] != "FixedRatio":
            raise ValueError("Unsupported serving rule; isolate the target manually")
        frozen.append(
            FixedRatioVersionSelectionRule(
                agent_version=version, traffic_percentage=rule["traffic_percentage"]
            )
        )
    selector = VersionSelector(version_selection_rules=frozen)
    expected = selector.as_dict()
    state.update(
        phase="freeze_serving_routes",
        previous_serving_selector=previous,
        frozen_serving_selector=expected,
        archive_sha256=hashlib.sha256(code).hexdigest(),
        candidate_id=candidate_id,
        baseline_code_sha256=baseline.definition.code_configuration.content_hash,
    )
    write_json(receipt, state)
    if previous != expected:
        api.agents.update_details(
            agent_name=name, agent_endpoint=AgentEndpointConfig(version_selector=selector)
        )
    if api.agents.get(agent_name=name).agent_endpoint.version_selector.as_dict() != expected:
        raise RuntimeError("Ordinary traffic was not frozen; refusing candidate creation")
    definition = copy.deepcopy(baseline.definition)
    for key in ("MAI_MODEL_DEPLOYMENT_NAME", "AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        if key in definition.environment_variables:
            definition.environment_variables[key] = profile["model"]
    for key in (
        "OPTIMIZATION_CONFIG",
        "OPTIMIZATION_CANDIDATE_ID",
        "OPTIMIZATION_RESOLVE_ENDPOINT",
    ):
        definition.environment_variables.pop(key, None)
    definition.code_configuration = CodeConfiguration(
        runtime=baseline.definition.code_configuration.runtime,
        entry_point=baseline.definition.code_configuration.entry_point,
        dependency_resolution=baseline.definition.code_configuration.dependency_resolution,
    )
    state.update(phase="upload_candidate_code", status="submitting")
    write_json(receipt, state)

    def verify_routes():
        current = api.agents.get(agent_name=name).agent_endpoint.version_selector.as_dict()
        if current != expected:
            if current == previous:
                api.agents.update_details(
                    agent_name=name, agent_endpoint=AgentEndpointConfig(version_selector=selector)
                )
            state["status"] = "serving_route_changed"
            write_json(receipt, state)
            raise RuntimeError("Serving routes changed unexpectedly; review before evaluating")

    with archive.open("rb") as handle:
        version = api.agents.create_version_from_code(
            agent_name=name,
            definition=definition,
            code=handle,
            code_zip_sha256=state["archive_sha256"],
            metadata={
                **(baseline.metadata or {}),
                "hillclimb_candidate": candidate_id,
            },
        )
    state.update(version=version.version, status=version.status, phase="wait_candidate_active")
    write_json(receipt, state)
    verify_routes()
    deadline = time.monotonic() + timeout
    while version.status != "active":
        if version.status == "failed" or time.monotonic() >= deadline:
            state["status"] = version.status
            write_json(receipt, state)
            raise RuntimeError(
                "Candidate not active; reconcile the recorded version, do not recreate"
            )
        time.sleep(5)
        version = api.agents.get_version(agent_name=name, agent_version=version.version)
    verify_routes()
    state.update(
        status="active",
        phase="candidate_ready",
        agent_name=name,
        isolated=True,
        candidate_profile_sha256=state["config"]["profile_sha256"],
    )
    write_json(receipt, state)
    return state

# Set up a hosted agent

This is a complete setup checklist, not a requirement to load another skill.
Reuse existing approved resources and application code wherever appropriate.

## 1. Discover capabilities and choose a runtime

- Inspect the selected source root and runtime/dependency manifests.
  Determine language, framework, entry point, protocol, port, required
  environment variables, and external tools. Classify secret references
  separately from public configuration.
- For a new application, resolve a supported framework/protocol with the
  user. Python with a Responses-compatible adapter is a reasonable starting
  point, not a requirement for every use case.
- Browse the current [official hosted-agent samples](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples).
  Select a real sample appropriate to the language/framework, pin its commit,
  and bring only the needed files into the chosen project. Follow its license.
  Do not assume that a remembered directory or SDK class still exists.
- Inspect the current [hosted-agent platform contract](https://learn.microsoft.com/azure/ai-foundry/agents/concepts/hosted-agents?view=foundry).
  Use the protocol/version actually implemented by the application, not an
  unrelated example's version.

If Azure MCP is available, discover the Foundry router's current schemas and
the applicable project/model/agent operations. Without it, inspect installed
CLI help and current SDK documentation:

```powershell
az version
azd version
azd ai agent --help
azd ai agent init --help
azd deploy --help
```

Record the tested versions. Install a missing required dependency only with
the user's approval and in the project's isolated environment.

## 2. Connect or provision project and models

Resolve the subscription, region, resource group, project endpoint, model
version/deployment, hosting resources, identity and network requirements.
Check model availability, quota/capacity and deployment SKU before promising
a deployment. A model's presence in a catalog does not imply RFT access.

For existing resources, verify ownership and access. For new resources,
present the proposed resource plan, region, expected charges, identities and
cleanup policy; obtain approval before creation. Use discovered MCP schemas,
current CLI commands, or an inspected Bicep/Terraform plan. Do not guess a
project-creation API or automatically create duplicate projects.

With Azure CLI/azd, authenticate using the approved identity:

```powershell
az login
az account show
azd auth login
```

Select the intended subscription explicitly if the returned account differs.
Set actual project variables in the selected azd environment or process;
`.env` files are not universally loaded automatically.

## 3. Implement and test locally

Adapt the chosen agent to the use case. Keep instructions, tools, skills and
model selection configurable so optimization can change them without
rewriting the service. Verify both the normal tool loop and termination,
refusal/escalation, and failure behavior.

Install compatible hosting adapter/SDK versions from their current package
registries, pin tested versions, and validate local startup. Send a real
protocol-shaped smoke request. A successful import is not a successful
invocation; missing credentials, permissions, or tool access are blockers,
not an excuse to report success.

Use bounded smoke tests. Stop only the temporary process you started after
testing. Avoid mutating shared production tool state.

### Native web search in hosted code

For a hosted custom tool loop, inspect the current
[Foundry web-search guidance](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/web-search?pivots=python)
and available Toolbox APIs. The live MAI exercise used a `WebSearchToolboxTool`
exposed by a versioned Foundry Toolbox MCP endpoint, not a replacement HTTP
search scraper or an assumed prompt-agent `WebSearchTool` integration.
Discover its actual tool name and argument schema before wiring the domain
adapter. The tested MCP path required an Entra token for
`https://ai.azure.com/.default`, the
`Foundry-Features: Toolboxes=V1Preview` header, and call metadata containing the
resolved policy deployment name, model name and model version in `model_info`.
Keep the model identity and optional domain filters explicit. Preserve returned
text, annotations/citations and actual search queries in authorized tool traces.
Do not infer tool-side tokens or a zero grounding charge from missing usage.
Review the service's grounding data-transfer terms before sending private data.

## 4. Initialize and choose deployment mode

After approval, an existing project and model can initialize a hosted agent
from the selected source without relying on another skill:

```powershell
azd ai agent init `
  --kind hosted `
  --src <source-directory> `
  --agent-name <unique-agent-name> `
  --project-id <existing-project-ARM-id> `
  --model-deployment <existing-model-deployment> `
  --deploy-mode code `
  --runtime <supported-runtime> `
  --entry-point <entrypoint-file> `
  --protocol <implemented-protocol> `
  --no-prompt
```

These flags were checked against azd help; check them again for the installed
release. Resolve all placeholders first. A repeated agent name creates a new
version of that agent, not a separate baseline. Do not use `--force` to
overwrite an existing configuration without approval.

Use code/ZIP deployment only for supported languages/runtimes, with the chosen
dependency resolution mode and an inspected `.agentignore`. Container/image
deployment is another supported path; do not require Docker/ACR work for a
code-only application. A prebuilt image can use `--image`, with the approved
registry connection and immutable digest. For a new project/model, use the
approved provisioning path and explicitly select the MAI model; do not accept
an unrelated interactive model default.

## 5. Package, authorize, deploy

Use the project's current `azure.yaml` or verified hosted-agent definition.
The manifest must match entry point, environment keys, protocol, container
port, supported runtime, CPU/memory and model endpoint. Inject secrets
through approved secret stores/environment references, not source files.

Refresh vendored code idempotently. For a container, build the supported platform (commonly
`linux/amd64`), tag uniquely by commit/run ID, and record the image digest.
Never deploy an unverified `latest` image as experiment evidence.

For ACR, inspect its permission mode: ABAC registries need applicable
repository-scoped roles; do not assume `AcrPull` works for every registry.
Verify the pull identity and least-privilege access to models, tools, storage,
and retrieval backends. Hosted invocation may require both the project and
per-agent identities to have `Azure AI User` at the appropriate account
scope; verify current platform requirements and actual assignments.
Permission changes require explicit authorization.

Once the inspected plan and configuration are approved, an azd project can
use its ordinary deployment path:

```powershell
azd deploy
```

Use `azd up` only when provisioning is also approved. In an SDK/REST path,
retrieve the current hosted-agent definition schema before creating a
version. The Python `azure-ai-projects` client exposes agent version creation
in supported releases; verify installed method/model signatures rather than
copying payloads from a different release.

## 6. Verify the deployed version

Retrieve the exact version and wait for an active/ready state with a bounded
timeout. Preserve failure logs. Invoke that version with an isolated hosted
session using its supported protocol. Keep the session sticky within a
conversation, and separate it from conversation history identifiers.

Verify output, actual model/tool access and required side effects. Record
version, digest, resource IDs, identities, smoke input/output, configuration,
and cost. Do not treat a queued deployment or an HTTP health check alone as
a completed deployment.

Official guidance: [hosted agents](https://learn.microsoft.com/azure/ai-foundry/agents/concepts/hosted-agents?view=foundry)
and [Foundry samples](https://github.com/microsoft-foundry/foundry-samples).
Do not substitute a classic prompt-agent quickstart for the current hosted-agent contract.

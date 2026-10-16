# Executable journey

## Contents

- [Start and adapt](#start-and-adapt)
- [Provision and deploy](#provision-and-deploy)
- [Evaluate and optimize](#evaluate-and-optimize)
- [Calibrate and run RFT](#calibrate-and-run-rft)
- [Select and seal](#select-and-seal)
- [Resume and clean up](#resume-and-clean-up)

Everything below ships in this skill. Python 3.11+ is required. The offline
helpers need no packages; model/cloud paths use the bundled dependency files.
The incident-triage example demonstrates interfaces, **not** a useful training
dataset or evidence that RFT improves a model. Replace its domain and data.
The dated [acceptance record](../examples/acceptance-results.json) distinguishes
live verification from mocked paths and records the rejected optimizer candidate.

## Start and adapt

### Existing-agent SDK happy path

For an existing optimization-ready agent, fill
[the minimal built-in configuration](../examples/cloud.builtin.example.json)
with the actual project, immutable baseline version, deployed models and a
prepared optimizer-only dataset. Keep existing configuration files; do not
overwrite them with template placeholders. The full cloud example remains
available for provisioning, custom rubrics and RFT.
Use the matching local baseline source/configuration (`instructions.md`,
`tools.json`, `metadata.yaml`), not an unrelated empty working directory.
The built-in path also accepts the official single-turn JSONL shape
(`name`, `query`, optional string `ground_truth` and `criteria`). If split
annotations are present they must be `optimizer`. For unannotated files,
review group-disjointness yourself: selecting a file as search data is not
proof that it is isolated from development or final data.

With `S`, `P`, `PY` and `C` resolved as below, use the same native SDK path
throughout:

```powershell
& $PY $C smoke --project $P --config "$P\cloud.json" `
  --receipt "$P\smoke.json" --execute --approve-billable
& $PY $C optimize --project $P --config "$P\cloud.json" `
  --receipt "$P\optimizer.json" --execute --approve-billable
& $PY $C optimizer-monitor --project $P --config "$P\cloud.json" `
  --receipt "$P\optimizer.json" --execute
& $PY $C optimizer-results --project $P --config "$P\cloud.json" `
  --optimization-receipt "$P\optimizer.json" --output "$P\native-results.json" `
  --receipt "$P\native-export.json" --execute
& $PY $C deploy-candidate --project $P --config "$P\cloud.json" `
  --optimization-receipt "$P\optimizer.json" --candidate-id "<nominated-candidate-id>" `
  --receipt "$P\candidate-deploy.json" --execute --approve-billable
```

First inspect plans without `--execute`. `smoke` is an ordinary invocation:
no grader, evaluation metadata or signing-key lookup. Built-in optimization
registers frozen task data, preserves per-task criteria, and requires no local
rubric or custom-evaluator validation receipt. Single-turn tasks use plain user
queries; history/context envelopes still require an isolated evaluation agent.
Use precise task criteria and validate project quality independently.

Candidate application uses the public SDK code-upload API, not a CLI-local
"last optimization job" value. It supports the bundled code-based starter's
root metadata and default optimization directories; custom config directories
and image-only agents fail explicitly rather than silently ignoring mutations.
Instructions, model, tools and skill bodies are materialized in the uploaded
configuration. Existing `@latest` traffic rules are frozen to their current
versions before creating the evaluation version; other serving rules are
preserved. Ordinary traffic does **not** switch to the candidate. Use the
returned exact version for held-out evaluation and promote separately only
after acceptance.

Read-only monitoring retries transient failures within a bound and persists
`monitor.disconnected` separately from remote job status. Resume the same
receipt after access recovery. Submission/upload ambiguity requires
reconciliation, not a different receipt or another job.

Set absolute paths in PowerShell; `P` is a new agent project, not the skill:

```powershell
$S = "<installed-skill-directory>"
$P = "<new-agent-project-directory>"
$J = Join-Path $S "scripts\journey.py"
$C = Join-Path $S "scripts\cloud.py"
python $J scaffold --output $P
```

The scaffold refuses existing destinations and includes a project-local
`azure.yaml` so azd cannot accidentally initialize in a parent project.

| File | Required adaptation |
|---|---|
| `domain.py` | Tool schemas and `execute_tool(name, arguments)`; use approved read-only or isolated tool state |
| `tools.json` | Keep the same function schemas as the domain hook, in Chat Completions format; runtime converts them to Responses format |
| `instructions.md` | Selected agent policy; keep tool/environment constraints explicit |
| `grader.py` | `grade(sample, item) -> float` in [0,1]; implement the real quality definition and hard safety gates |
| `evaluator.md` | Optimizer judge rubric; keep its score meaning aligned with the canonical grader |
| `cases.example.jsonl` | Replace with real, reviewed cases and connected-group identifiers |
| `labels.example.jsonl` | Replace with human-reviewed good/bad/adversarial responses, including reward-hacking attempts |
| `tool-probes.example.jsonl` | Probe every tool, representative arguments, boundaries, and isolated state |

For unstructured or semantic tasks, replace the JSON-field grader with the
bundled `semantic_grader.py` (copy it to `grader.py`) and customize
`judge-rubric.md`. Set `MAI_JUDGE_BASE_URL`, `MAI_JUDGE_DEPLOYMENT_NAME` and
`MAI_JUDGE_PRICING_KEY`; use Entra or the optional `MAI_JUDGE_API_KEY`.
The evaluation adapter records these judge calls separately from serving.
A Python training grader cannot call a judge or external API:
use the endpoint path for such rewards. Do not reward a valid format instead
of task correctness. The example's positive field weights give partial credit;
its invariants override partial credit.
It also supports `required_tool_calls` (ordered subset matches on name,
arguments and/or result) and `forbidden_tools` as zero-reward process gates.

Each case has `id`, `group`, `split`, `messages` ending in a user turn, and
reference fields needed by the grader. Split names are `optimizer`, `train`,
`validation`, `development`, `final`. Assign connected groups before splitting;
renaming IDs does not make related documents/customers/sessions independent.
The helper rejects shared groups and identical messages across splits.
Use `context` for model-visible documents/state; keep expected answers,
criteria and other references outside it. Optimization sends the complete
message history/context in a versioned envelope and reference data separately
as `ground_truth`. Only an opted-in evaluation deployment unwraps workload
envelopes, and remote envelopes can never introduce system/developer roles.
Prepared training policies are represented by a hash checked against the
server's configured policy, not by caller-supplied system instructions.

```powershell
python $J prepare --data "$P\cases.jsonl" --tools "$P\tools.json" `
  --instructions "$P\instructions.md" --output "$P\prepared"
```

This freezes separate files and a hash manifest and generates query-shaped
optimizer rows. The explicit `--instructions` path adds selected system
instructions to training/validation rows and rejects conflicts; do not use that
format for a training service that disallows system messages.
Prepare again in a **new** directory after selecting a different policy;
recalibrate that data. Never put reference answers into prompts merely to
satisfy an evaluator.

For RFT, prepare from the **resolved selected policy**, including skill bodies:
use `--policy-adapter "$P\agent.py"` instead of `--instructions`. This requires
runtime packages and actual model configuration to be set first. Task context
is materialized into training messages exactly once. MAI reinforcement examples
remain system-message-free; the manifest binds the resolved policy fingerprint,
and submission verifies that fingerprint against calibration and current
runtime policy. Existing system messages are rejected before upload.
Calibration receipts must
record the same model, endpoint, instructions, tools, domain implementation,
runtime limits and implementation fingerprint; changing any requires new
rollouts and calibration.

Install cloud/runtime dependencies in the chosen isolated environment:

```powershell
python -m venv "$P\.venv"
$PY = Join-Path $P ".venv\Scripts\python.exe"
& $PY -m pip install -r "$P\requirements.txt" -r "$S\scripts\requirements-cloud.txt"
Copy-Item "$S\examples\cloud.example.json" "$P\cloud.json"
```

Fill actual IDs, endpoints, versions, model format/SKU/capacity, unique resource
names, and evaluator names in `cloud.json`. Store only environment-variable
**names** for secrets. Verify CLI/model/region access and prices before approval.

**Separate evaluation from ordinary serving.** Production defaults to
`enable_evaluation: false`: workload-looking user text stays a user message,
and request metadata cannot enable trace export. For hosted benchmarking and
optimizer workloads, use an isolated evaluation agent with
`enable_evaluation: true` and a versioned `evaluation_key_secret_uri` in
`cloud.json`. This stores only a Key Vault reference in the agent manifest,
never the signing key. Use a separately generated high-entropy signing key
(at least 32 random bytes), not the grader/tool bearer token.

After deployment returns the evaluation agent identity, give that identity
**Key Vault Secrets User** at the approved secret scope before invoking it.
The evaluation client also needs read access. Use the approved RBAC workflow,
not an Owner grant or a directory-wide permission. Local-only testing can use
`HILLCLIMB_EVALUATION_KEY` from an approved process secret source; never put its
value in `cloud.json`, a command argument, dataset or source file.

## Provision and deploy

All cloud mutations are **dry runs** unless both `--execute` and
`--approve-billable` are supplied. A receipt is created before a cloud request.
Do not change its path to bypass a pending/ambiguous submission.

For a new project, first create the explicitly approved resource group, then
run `cloud.py provision` (bundled `project.bicep`) and `deploy-model`. For an
existing project/model, skip both. Resolve outputs into `cloud.json`; never
create resources simply because an example contains a placeholder.
Before agent creation, verify data-plane access: ARM Owner/Contributor alone
does not grant it. If needed, `grant-project-access` grants **Foundry Project
Manager** to the explicit developer object ID at the project scope after
approval. Set the developer object ID/type in configuration; no directory
lookup is performed. Account-model inference may also require
`grant-inference` for the developer, then separately for the new agent.

```powershell
& $PY $C provision --project $P --config "$P\cloud.json" --receipt "$P\provision.json"
& $PY $C deploy-model --project $P --config "$P\cloud.json" --receipt "$P\model.json"
# Review each plan, then repeat the approved command with:
# --execute --approve-billable
& $PY $C init-hosted --project $P --config "$P\cloud.json" `
  --receipt "$P\init.json" --execute --approve-billable
& $PY $C deploy-hosted --project $P --config "$P\cloud.json" `
  --receipt "$P\deploy.json" --execute --approve-billable
```

`init-hosted` generates current azd configuration and wires runtime environment,
model metadata, and a two-minute idle timeout. The packaged runtime listens on
8088 and implements Responses through the pinned adapter. Set the returned
`agent_version` and newly created agent identity as `inference_principal_id`
(`inference_principal_type: ServicePrincipal`) in the configuration.

Verify the **actual model endpoint** from inside the hosted agent. Project and
account inference routes are not interchangeable for every model. In live MAI
acceptance the account route worked, while the project-scoped inference route
returned 400 even for the developer identity. Account-level inference required
the new agent identity to have **Cognitive Services OpenAI User** at the account
scope. If that is the verified minimal permission for the selected endpoint:

```powershell
& $PY $C grant-inference --project $P --config "$P\cloud.json" `
  --receipt "$P\inference-role.json" --execute --approve-billable
```

The role ID is explicit/configurable; do not substitute Owner. This assignment
uses the known principal ID and does not need a directory name lookup. An
equivalent account-scoped Bicep template is bundled. Retain its assignment ID
for cleanup. Other tools/resources may need different least-privilege roles.

Set runtime variables in the process (a `.env` file is not automatically read):

```powershell
$env:MAI_OPENAI_BASE_URL = "<verified-model-openai-v1-endpoint>"
$env:MAI_MODEL_DEPLOYMENT_NAME = "<deployment>"
$env:MAI_PROJECT_ENDPOINT = "<project-endpoint>"
$env:MAI_HOSTED_AGENT_NAME = "<agent-name>"
$env:MAI_HOSTED_AGENT_VERSION = "<exact-active-version>"
```

`MAI_MODEL_DEPLOYMENT_NAME` bootstraps an unconfigured agent. A loaded candidate's
model takes precedence. To deliberately compare a different checkpoint, set
`MAI_MODEL_OVERRIDE`; this explicit override is recorded in policy provenance.
Do not assume changing a deployment environment variable overrides a loaded
candidate. Clear the override before comparing the original policy.
For deployment, set the equivalent explicit `model_override` in `cloud.json`
when pinning a checkpoint over a loaded candidate. `deploy-hosted` rewires the
manifest/model metadata from the selected configuration before packaging; a
local process override alone is not a deployed configuration.

Invoke with `hosted_eval.py` using the evaluation command below. It creates an
exact-version session, invokes it, and stops its compute even on failure.
Readiness alone is not a successful smoke test.
For that privileged client, set `HILLCLIMB_EVALUATION_KEY_SECRET_URI` to the
same versioned secret reference. It sends short-lived input-bound HMAC
signatures, not the signing key. Capture is checked before inference, requires
the server opt-in as well as the signature, and rejects expired/tampered
requests and replayed nonces within the worker. Keep these endpoints isolated;
the process-local replay cache is not a distributed authorization service.
For an ordinary production smoke invocation, use the normal Responses/azd
invoke path instead of requesting an evaluation trace.

## Evaluate and optimize

Use `agent.py` for per-model-call usage and tool traces, or `hosted_eval.py` for
the real hosted path. An existing project can supply its own
`run_episode(item) -> {output_text, output_tools, calls}` adapter. Each call
records model, purpose, and raw Responses usage. Never fabricate absent usage.
For RFT, custom adapters must also return `policy_contract` or expose a
`policy_contract()` hook describing the resolved model, instructions, tools,
domain implementation and runtime settings. Missing or mixed policy provenance
blocks calibration. The bundled local and hosted adapters already implement it.

```powershell
& $PY $J evaluate --data "$P\prepared\development.jsonl" `
  --adapter "$P\agent.py" --grader "$P\grader.py" `
  --stage baseline --rollouts 3 --price-key policy --output "$P\baseline.jsonl"
& $PY "$S\scripts\cost_report.py" --receipts "$P\baseline.jsonl" `
  --prices "$P\prices.json" --output "$P\baseline-cost.json"
```

Freeze a source-backed `prices.json` using the pricing reference, not the
synthetic example. Serving usage, judge/optimizer calls, training and hosting
are different costs. The bundled hosted evaluator requests an evaluation-only
trace envelope, preserving tool names/arguments/results and per-model-call
usage and policy identity. Ordinary user calls still return only the answer.
Rich traces require the signing authorization above. Common credential-named
tool/call fields are redacted, but authorized traces still contain private
instructions, skill content, endpoints and domain records needed for evaluation.
Protect the resulting artifacts and use synthetic or approved data; this is
not a general-purpose PII scrubber.
An older or custom hosted runtime without this contract fails explicitly;
it must implement the trace adapter rather than invent an empty tool trace.
Runtime failures score zero, stay in the
denominator, emit explicit errors, and make `evaluate` exit nonzero.
For a smoke test or a bounded collection worker, repeat `--case-id` to select
explicit cases while retaining the full source dataset fingerprint. It is
not allowed on the sealed final split. A parallel collector must aggregate
and verify the complete planned case/rollout set before comparing stages;
filtered samples must not masquerade as a complete benchmark.

Agent Optimizer documents **built-in evaluators and custom rubric evaluators**,
not arbitrary code graders. See the current
[optimizer evaluator support](https://learn.microsoft.com/azure/foundry/agents/how-to/create-optimizer-dataset#customize-evaluators-advanced).
Successful ordinary evaluator calibration does not establish optimizer support.
Use `evaluator_type: builtin` for the simple SDK path above. For a custom prompt
rubric, select `evaluator_type: prompt`, register and calibrate `evaluator.md`,
then submit the explicit spec below.

For **ordinary reference-checkable evaluation**, `optimizer.evaluator_type: code`
registers the canonical `grader.py` with a bundled transport adapter. This setting
does **not** authorize an optimizer submission: `optimize` rejects code evaluators.
Keep the
grader's local `grade(sample, item)` contract: `sample.output_text` is the
answer and `item` is the original reference. The generated catalog entry point
decodes mapped `response` content-block frames and serialized `ground_truth`
before calling that unchanged grader. Known-response validation deliberately
uses the same content-block framing seen in optimizer output; both grader
and adapter fingerprints bind the validation evidence. Do not teach domain
grading logic to interpret service transport arrays as application answers.
Use a pure, sandbox-compatible implementation; even some standard-library
modules such as `urllib.parse` are disallowed. Validate known-response scores remotely before relying on
ordinary evaluation results. Use the canonical grader for independent baseline,
candidate and final quality comparisons; a supported optimizer judge can nominate
candidates without defining their final acceptance. `max_concurrent_agent_runs` bounds native search evaluation
concurrency independently from the number of candidate configurations.

```powershell
& $PY $C register-evaluator --project $P --config "$P\cloud.json" `
  --receipt "$P\evaluator.json" --execute --approve-billable
# Set optimizer.evaluator_version to the returned version.
& $PY $C validate-evaluator --project $P --config "$P\cloud.json" `
  --labels "$P\reviewed-labels.jsonl" --receipt "$P\evaluator-validation.json" `
  --execute --approve-billable
& $PY $C optimize --project $P --config "$P\cloud.json" `
  --evaluator-validation "$P\evaluator-validation.json" `
  --receipt "$P\optimizer.json" --execute --approve-billable
& $PY $C optimizer-monitor --project $P --config "$P\cloud.json" `
  --receipt "$P\optimizer.json" --timeout 3600 --execute
```

The documented azd CLI remains an alternative for a CLI-managed project:

```powershell
Push-Location $P
azd ai agent optimize --config eval.yaml `
  --project-endpoint "<verified-project-endpoint>" `
  --evaluator builtin.task_adherence `
  --eval-model "<evaluation-deployment>" --optimize-model "<optimization-deployment>" `
  --max-candidates 2 --no-wait --no-prompt
Pop-Location
```

Prepare `eval.yaml` using the
[documented schema](https://learn.microsoft.com/azure/foundry/agents/how-to/optimize-agent-targets#configure-the-optimization-run):
bind the exact hosted version, policy deployment, existing baseline metadata,
optimizer-only dataset and explicit models. In an initialized azd project,
`agent.config` points to that baseline metadata. A standalone directory without
azd project files requires the documented interactive instruction flow instead;
do not silently omit baseline instructions with `--no-prompt`.
Use precise per-task criteria where required, preserve the returned operation ID,
and independently score nominations with `grader.py`. Task adherence is not proof
of factual accuracy, source faithfulness, safety or cost savings.

The helper bundles the instruction/config binding, immutable baseline version,
query-shaped search dataset, evaluator and candidate limit. Optimizer catalog
bindings currently expose `query`, `response`, `tool_calls`, `tool_definitions`;
do not register a `sample/item` evaluator as an optimizer evaluator. The
registered prompt evaluator uses `query/response` and returns `{"result": score}`.
It can use `ground_truth` for each example's references. Configure canonical
runtime fields in `optimizer.data_mapping`; unsupported aliases are rejected.
Skills from the metadata's `skill_dir` (default `skills`) are included with
their names, descriptions and bodies. Hosted optimizer service tool telemetry
may still depend on platform instrumentation; independently validate process
quality using the explicit trace adapter rather than trusting absent traces.
The custom-rubric SDK path explicitly supplies its judge deployment, threshold and version;
setting only an optimizer-wide eval model is insufficient for custom evaluators.
Known-label validation must complete with actual numeric scores and acceptable
MAE before search. An evaluator 401 or null score is **not** a model score of zero.

Inspect candidates and usage in the recorded job result. Populate the chosen
`candidate_id`; `deploy-candidate` requires the optimization receipt and deploys
an isolated SDK version with approval, preserving ordinary serving routes.
The runtime resolves optimizer configuration with Entra auth and refuses to
silently use baseline when a requested candidate was not loaded. Independently
evaluate every nominated candidate on the **same development cases/rollouts**
with the canonical grader, then run:

For the bundled local adapter, export a nomination without deploying it:

```powershell
& $PY $C export-candidate --project $P --config "$P\cloud.json" `
  --optimization-receipt "$P\optimizer.json" --candidate-id "<candidate-id>" `
  --output "$P\candidate-config.json" --receipt "$P\export.json" --execute
$env:OPTIMIZATION_CONFIG = Get-Content "$P\candidate-config.json" -Raw
# Run evaluate with the same agent.py, data, grader and rollout count as baseline.
# Clear OPTIMIZATION_CONFIG before evaluating or selecting the original baseline.
```

Inline config is supported by the bundled optimization adapter but deprecated
upstream; use deployed candidate versions or its local-directory/resolver path
for production. The exported source is a nomination, not a selected winner.

```powershell
& $PY $J compare --baseline "$P\baseline.jsonl" --candidate "$P\candidate.jsonl" `
  --output "$P\comparison.json" --min-quality-gain 0 --max-latency-ratio 1.2
```

This checks matched coverage and grader/data hashes and computes a paired
connected-group bootstrap interval. Its quality/latency gate is not a winner:
safety review and matched cost evidence still require explicit acceptance.

Native `optimizer-results` is search evidence, not this independent comparison.
It joins persistent case IDs/query-reference fingerprints, records adaptive
coverage, and separates the optimizer composite from shared evaluator scores.
It does not turn missing unscheduled cases into failures or native token
averages into matched serving-cost savings.

The runtime preserves requested and returned model identity, provider completion
status and incomplete details. Incomplete generations fail explicitly with
observed usage. Authorized rich-capture failures use `response.failed`, never a
successful answer carrying a hidden error. Failed hosted evaluations collect
bounded, allowlisted diagnostics before cleanup; opaque console text is not
exported because it can contain credentials. If a proxy discards the failure
payload, usage remains unknown. Ordinary users still cannot request private
failure traces.
Existing deployed agents need the updated runtime redeployed to emit the new
provider metadata and authorized partial-usage failure payloads; upgrading the
local skill alone does not change remote code.

## Calibrate and run RFT

RFT is justified only for remaining model-policy errors, not infrastructure
failures, missing tools, weak evaluation, or an already solved fixture.
Freeze the selected configuration and prepare matching training instructions.
Evaluate **train and validation** with at least two rollouts per case; retain
both outputs (including failures) in one calibration-receipts JSONL file.

```powershell
& $PY $J calibrate --receipts "$P\calibration-receipts.jsonl" `
  --grader "$P\grader.py" --labels "$P\reviewed-labels.jsonl" `
  --output "$P\calibration.json"
```

Defaults require 10 successful cases, repeated within-example reward variation,
good/bad/adversarial reviewed labels, and label MAE <=0.1. These are starter
gates, not universal statistical proof: set domain thresholds before seeing
results. A constant reward or failed parity blocks submission. Test omissions,
incorrect tool arguments, injection, truncation and fluent-but-wrong answers.
Do not weaken a rubric merely to manufacture variation.
For a semantic grader, observed regrading disagreement is bounded by the declared
MAE tolerance rather than requiring exact deterministic equality. Calibration
judge calls are recorded in the calibration report for the experiment ledger.

For endpoint grading/tool calling, provision the bundled authenticated service:

For Key Vault secrets or a private prebuilt image, bootstrap a **new** app in
an existing approved Container Apps environment first:

```powershell
& $PY $C bootstrap-endpoints --project $P --config "$P\cloud.json" `
  --receipt "$P\endpoint-bootstrap.json" --execute --approve-billable
```

This creates an identity-enabled placeholder, not a grader. It refuses to replace
an existing app. Read its principal from the receipt and grant **Key Vault Secrets
User** at the exact approved secret ARM scope before binding the secret. For a
private image, configure that identity's approved registry pull permission:
`AcrPull` for legacy registry RBAC, or the appropriate repository-scoped reader
role for an ABAC registry. Do not enable registry admin or introduce a password
to work around a managed-identity pull failure.

Build the adapted Dockerfile, including every domain helper/data asset, using
the approved registry (`az acr build --registry <registry> --image <unique-tag> $P`).
Resolve its digest with `az acr repository show`, then set `service_image` to
`<registry-host>/<repository>@sha256:<digest>` and `service_registry` to its host.
The prebuilt-image path updates the existing app with system-identity pull;
it does not use `containerapp up`, which can request registry credentials even
when identity-based pull is configured.

```powershell
$env:HILLCLIMB_ENDPOINT_TOKEN = "<secret-from-approved-secret-store>"
& $PY $C deploy-endpoints --project $P --config "$P\cloud.json" `
  --receipt "$P\endpoints.json" --execute --approve-billable
# Put the returned HTTPS service URL in cloud.json.
& $PY $C endpoint-parity --project $P --config "$P\cloud.json" `
  --receipt "$P\parity.json" --labels "$P\reviewed-labels.jsonl" `
  --tool-probes "$P\tool-probes.jsonl" --execute --approve-billable
```

The service shares the exact `grade` and domain hooks, requires bearer auth,
limits requests/outputs to 1 MB, returns **only `{"score": number}`** on `/grade`,
and preserves `call_id` on `/tools/<tool-name>`. A routed callback does not require
the caller to duplicate the tool name in its body; a conflicting name is rejected.
The legacy name-in-body `/tools` route remains available. Invalid input is
explicit 4xx, not a free reward.
The deployment uses secret references and 2-5 replicas; inspect capacity,
concurrency and external tool side effects for your workload. In a partial
deployment, reconcile the registry build and set `service_image` (immutable
digest) and `service_registry` to reuse it instead of rebuilding.
Prefer a versioned `endpoint_token_secret_uri` for the grader/tool service:
the CLI receives a Key Vault reference rather than the bearer token in its
arguments. Its system identity needs secret-read permission before secret
configuration. The process-environment fallback remains available for isolated
development, but exposes the value to local process inspection while the CLI
runs. Timeouts/launch failures suppress credential-bearing subprocess details;
error receipts retain the reconciliation requirement, not the command secret.
For semantic endpoints, set the public judge configuration in
`endpoint_environment`. Deployment assigns a system identity; grant only its
required model-inference role using the returned identity as
`inference_principal_id`. No API keys belong in this JSON. Use
`rft.parity_tolerance` for predeclared semantic-score tolerance (default zero
for deterministic parity), and calibrate it before training.

Direct HTTP parity is not proof of the Foundry callback contract. With `rft`
configured, `endpoint-parity` also validates and executes the grader through
Foundry's actual grader API on the reviewed labels. It rejects metadata error
flags even when the API returns numeric zero. RFT submission requires this
evidence, bound to the endpoint, image and grader specification. Re-run it after
any change; an older HTTP-only parity receipt is insufficient.
Project-local Python helpers are loaded in an isolated project namespace, so
lazy sibling imports work without borrowing another project's cached module.
Keep nonstandard service assets in the Dockerfile and `.dockerignore` as well.

Confirm current RFT model/region/SKU and agentic-access support; record dated
capability evidence in configuration. Match each example's function schemas
to job tools. The helper checks disjoint groups, dataset hashes, selected system
instructions, eligible calibration, reviewed labels, and endpoint/tool parity:

```powershell
& $PY $C rft-submit --project $P --config "$P\cloud.json" --prepared "$P\prepared" `
  --calibration "$P\calibration.json" --receipts "$P\calibration-receipts.jsonl" `
  --labels "$P\reviewed-labels.jsonl" --parity "$P\parity.json" `
  --receipt "$P\rft.json"
# Inspect the token-free plan and estimate; execute only the approved eligible job.
# Add --execute --approve-billable to the same command.
& $PY $C rft-monitor --project $P --config "$P\cloud.json" `
  --receipt "$P\rft.json" --timeout 3600 --execute
& $PY $C rft-cancel --project $P --config "$P\cloud.json" `
  --receipt "$P\rft.json" --execute --approve-billable
```

Uploads and the job ID are persisted immediately. Mutating SDK calls do not
automatically retry. A monitoring timeout resumes the **same** job.
`rft-cancel` retrieves and verifies the recorded endpoint, model and job before
one cancellation request, persists the request first, then confirms terminal
cancellation through the same receipt. It refuses a different endpoint or
training target. Cancellation does not imply zero cost; record observed work
and current pricing evidence.
`grader_type: python` supports a self-contained non-network grader;
`grader_type: endpoint` adds runtime secret headers without storing them.
Neither model catalog membership nor a historical success proves current RFT
access. The tiny incident example is expected to fail learnability gates.
Capability evidence must name the exact training model, inference deployment,
region and training SKU, and be no older than seven days. This is an explicit
verified-access attestation: private agentic-RFT feature flags are not inferred
from a generic catalog listing. `rft.source_deployment` must match calibration.

Use `deploy-checkpoint --training-receipt "$P\rft.json"` after success, with
`model.name` set to a recorded checkpoint/model ID, the supported deployment
SKU, and a unique deployment name. Compare several justified checkpoints
using the same adapter/grader/development data, not training reward.

## Select and seal

First generate the matched quality/latency/cost/savings comparison described
in [pricing and cost](pricing.md#matched-savings-and-investment). It can compare
baseline, optimized and RFT stages together, importing optimizer and calibration
usage and explicit training/hosting charges. Unknown investment costs suppress
break-even and net-savings claims; cheap but unsuccessful tasks are not wins.

Create an acceptance JSON containing actual `candidate_sha256`,
`cost_report_sha256`, booleans `quality`, `safety`, `latency`, `cost`, and a
written `rationale`. It is a reviewed decision, not an automatically generated
permission slip. An incomplete cost report requires explicit
`accept_incomplete_cost: true` and `cost_rationale`; unknown is never free.

```powershell
& $PY $J select --candidate "$P\candidate.jsonl" --approval "$P\approval.json" `
  --cost-report "$P\candidate-cost.json" --adapter "$P\agent.py" --grader "$P\grader.py" `
  --final-data "$P\prepared\final.jsonl" --output "$P\selected.json"
& $PY $J evaluate --data "$P\prepared\final.jsonl" --adapter "$P\agent.py" `
  --grader "$P\grader.py" --stage "<selected-stage>" --rollouts 3 `
  --final-state "$P\selected.json" --output "$P\final.jsonl"
```

Selection verifies implementation/model, grader, cost-report and dataset
fingerprints. An exclusive seal is persisted before final inference; failures
do not unlock it. Do not reopen final data to choose a different candidate.
Deploy the selected exact version/weights and perform a separate smoke test.
The cost report must match the exact candidate receipt fingerprint, and its
metrics are recomputed from the frozen price snapshot and ledger. Matching
only the episode count is no longer sufficient.

## Resume and clean up

On any rejected/ambiguous cloud request, retain the receipt and reconcile jobs,
files, agent versions and resource inventory before a deliberate repair.
Do not blindly rerun a create. Capture hosted session logs for 500 responses;
the outer error can hide a model 401/400. Windows CLI launch/UTF-8 handling,
preview evaluator headers, and current tool schema conversion are bundled.
`optimizer-reconcile --operation-id <verified-id>` checks the saved target/run
before attaching a discovered job to a receipt. `optimizer-monitor` then resumes
that same job. Evaluator validation resumes its recorded evaluation/run IDs;
it refuses to create another run after an ambiguous submission.

List the exact resources created by the run, compare with the pre-run inventory,
and delete only approved test resources/role-assignment IDs. Stop/delete your
test sessions and agent; do not delete shared projects, models, registries or
environments. Record cleanup, unknown costs and anything not tested live.

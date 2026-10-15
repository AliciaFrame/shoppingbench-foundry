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

This freezes separate files and a hash manifest, generates query-shaped
optimizer rows, and adds selected system instructions to training/validation
rows only. It rejects conflicting existing system messages. Prepare again in
a **new** directory after selecting a different policy; recalibrate that data.
Never put reference answers into prompts merely to satisfy an evaluator.

For RFT, prepare from the **resolved selected policy**, including skill bodies:
use `--policy-adapter "$P\agent.py"` instead of `--instructions`. This requires
runtime packages and actual model configuration to be set first. Task context
is materialized into training messages exactly once. Calibration receipts must
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

Register the customized `evaluator.md`, then submit the explicit optimizer spec:

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
The SDK path explicitly supplies its judge deployment, threshold and version;
setting only an optimizer-wide eval model is insufficient for custom evaluators.
Known-label validation must complete with actual numeric scores and acceptable
MAE before search. An evaluator 401 or null score is **not** a model score of zero.

Inspect candidates and usage in the recorded job result. Populate the chosen
`candidate_id`; `deploy-candidate` deploys it as a new version with approval.
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

```powershell
$env:HILLCLIMB_ENDPOINT_TOKEN = "<secret-from-approved-secret-store>"
& $PY $C deploy-endpoints --project $P --config "$P\cloud.json" `
  --receipt "$P\endpoints.json" --execute --approve-billable
# Put the returned HTTPS service URL in cloud.json.
& $PY $C endpoint-parity --project $P --config "$P\cloud.json" `
  --receipt "$P\parity.json" --labels "$P\reviewed-labels.jsonl" `
  --tool-probes "$P\tool-probes.jsonl" --execute
```

The service shares the exact `grade` and domain hooks, requires bearer auth,
limits requests/outputs to 1 MB, returns `reward` on `/grade`, and preserves
`call_id` on `/tools`. Invalid input is explicit 4xx, not a free reward.
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
```

Uploads and the job ID are persisted immediately. Mutating SDK calls do not
automatically retry. A monitoring timeout resumes the **same** job.
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

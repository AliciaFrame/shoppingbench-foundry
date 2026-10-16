# Diagnose before retrying or spending again

| Symptom | Check and response |
|---|---|
| Model/project operation unavailable | Confirm endpoint type, SDK/API version, model/SKU and subscription capability. A project endpoint's missing `/models` route is not definitive evidence of model unavailability. |
| Hosted version not active | Retrieve the existing version's status and logs. Wait within a budgeted timeout; do not create duplicate versions to hide a failed build. |
| 401/403 | Verify Entra audience, caller/project/per-agent identities, current required roles and scopes. Ask an authorized administrator for missing assignments; never embed a long-lived token. |
| Image pull failure | Verify registry permission mode, repository-scoped access where applicable, pull identity and immutable image digest. Refresh vendored code before rebuilding. |
| Conversation/session confusion | Use the selected protocol's current session contract. Keep hosted routing sticky within a conversation and isolated between independent evaluation cases. |
| Tool failures | Check endpoint, schema, auth, network, timeout and state reset. Retain the episode and observed costs; distinguish a model mistake from unavailable infrastructure. |
| Optimizer runs but candidate has no effect | Verify the runtime actually reads generated configuration and the deployed version is the candidate being evaluated. |
| SDK-created candidate cannot deploy because azd has no last job | Use `deploy-candidate` with the recorded optimization receipt. The helper applies the candidate through the SDK and does not need CLI-local job context. Never create a replacement optimization job. |
| Candidate evaluation changes ordinary traffic | Use the isolated SDK deployment path, which resolves `@latest` rules to their current immutable versions before candidate creation. Keep those routes frozen until separate acceptance/promotion. |
| Local monitor stops while cloud job is running | Inspect `monitor.status` separately from remote job status. Resume the same durable job ID after auth/network repair; a monitor disconnect is not cloud failure. |
| Valid-looking answer was truncated | Inspect provider completion status and incomplete details. The runtime rejects incomplete generations and retains observed usage; do not count a partial answer as success. |
| Failed session has no usable logs after cleanup | The hosted evaluator captures bounded allowlisted diagnostic signals before stopping the session. Raw console text is withheld because it may contain secrets; signed failed responses preserve available calls. |
| Retrieved citations pass checks but claims are unsupported | Add claim/evidence faithfulness to the semantic rubric or source review. Calibrate with valid-link/unsupported-claim negatives; citation provenance and format alone are not correctness. |
| Higher optimizer/training score, worse task result | Compare with the frozen quality evaluator and inspect reward shortcuts, leaked labels, missing invariants or drift. Do not promote automatically. |
| RFT stuck waiting for capacity | Monitor the existing job, verify supported SKU/region and approved budget. A queued job is not justification for repeated submissions. |
| Grader throttling/errors | Measure latency and concurrency, align judge capacity and retry only transient transport failures. Do not return success-like scores on judge failure. |
| Direct endpoint parity passes but RFT rewards are zero | Validate and execute the exact grader through Foundry's grader API. The endpoint response must use the documented `score`-only object; inspect metadata error flags because infrastructure errors can accompany a numeric zero. |
| Private endpoint image asks for registry credentials | Bootstrap an identity-enabled app, grant the approved pull role, bind its registry with system identity, then update by immutable digest. Do not enable registry admin or pass a password as a managed-identity workaround. |
| Training callback tool receives no tool name in the body | Use a name-bound URL such as `/tools/<tool-name>` and verify every configured tool through the actual trainer-compatible request shape. Treat a conflicting body name as invalid. |
| RFT preprocessing rejects system messages | Prepare RFT with `--policy-adapter`, not `--instructions`. Keep examples system-free and bind the resolved selected policy through the manifest/calibration fingerprints. Regenerate and recalibrate the rows; never edit uploaded data in place. |
| Valid optimizer answers all score zero | Inspect mapped response framing and replay captured answers through the canonical grader. Use a documented built-in or custom rubric for optimization; the code transport adapter and framed-label calibration validate ordinary evaluation, not optimizer compatibility. |
| Native baseline repeatedly fails with zero outputs | Distinguish target execution from grading. Preserve native run IDs/errors, check exact-version invocation and session logs, and stop owned retrying jobs before another submission. |
| Project hostname returns NXDOMAIN | Check the resource and advertised endpoint through the Azure control plane, and compare DNS with an independent known host. A successful ARM state does not prove data-plane availability. Do not recreate shared resources, change machine DNS, or invent a replacement endpoint. Preserve cleanup-pending state if session stop cannot reach the data plane. |
| Higher tokens after RFT | Split ordinary/cached input, output/reasoning, replayed context and auxiliary calls. Training proxies are not actual cost measurements. |
| Prices unavailable or mismatched | Follow Azure-first then GitHub fallback, verify units/tier/provider, preserve unresolved rates. Do not guess zero or mix currencies. |
| Resume would mix runs | Compare code, prompts, tools, data, model/deployment, evaluator/judge and price fingerprints. Start a new output under a changed contract. |
| Final test was already consulted | Mark it unsealed. Do not reuse it for a new selection claim; obtain a new final holdout or disclose the limitation. |

Log failures explicitly and stop at an unresolved gate. Collect only the
minimal diagnostic data needed, redact secrets/private task content, and
avoid uploading diagnostics to third parties without authorization.

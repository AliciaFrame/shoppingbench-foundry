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
| Higher optimizer/training score, worse task result | Compare with the frozen quality evaluator and inspect reward shortcuts, leaked labels, missing invariants or drift. Do not promote automatically. |
| RFT stuck waiting for capacity | Monitor the existing job, verify supported SKU/region and approved budget. A queued job is not justification for repeated submissions. |
| Grader throttling/errors | Measure latency and concurrency, align judge capacity and retry only transient transport failures. Do not return success-like scores on judge failure. |
| Higher tokens after RFT | Split ordinary/cached input, output/reasoning, replayed context and auxiliary calls. Training proxies are not actual cost measurements. |
| Prices unavailable or mismatched | Follow Azure-first then GitHub fallback, verify units/tier/provider, preserve unresolved rates. Do not guess zero or mix currencies. |
| Resume would mix runs | Compare code, prompts, tools, data, model/deployment, evaluator/judge and price fingerprints. Start a new output under a changed contract. |
| Final test was already consulted | Mark it unsealed. Do not reuse it for a new selection claim; obtain a new final holdout or disclose the limitation. |

Log failures explicitly and stop at an unresolved gate. Collect only the
minimal diagnostic data needed, redact secrets/private task content, and
avoid uploading diagnostics to third parties without authorization.

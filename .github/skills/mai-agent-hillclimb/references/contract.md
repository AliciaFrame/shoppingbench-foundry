# Project and experiment contract

Resolve these values with the user and existing project metadata. Do not
silently fill gaps with a different project's configuration.

| Area | Record before paid work |
|---|---|
| Goal | Use case, allowed actions, success definition, safety constraints, escalation/failure behavior |
| Agent | Selected source root, language/framework, entry point, hosted protocol/version, code revision |
| Environment | Subscription, region, resource group, project endpoint, deployment names/versions, resource ownership |
| Tools | Schemas, endpoints, identities, side effects, tool-state reset and test isolation |
| Evaluation | Primary/secondary metrics, direction, evaluator/rubric/version, judge identity if used, failure metric values |
| Data | Optimization, training, validation, development and sealed-final paths/versions; connected group identifiers |
| Budgets | Inference/search/training/hosting caps, time limits, concurrency, approved role/resource changes |
| Acceptance | Minimum improvement or target, tolerated regression, cost/latency ceilings, uncertainty policy |
| Pricing | Currency, provider, model/version, region/SKU/tier, source/date, cache accounting, reference-vs-bill basis |

Use a current agent metadata format when the project already has one. Keep an
experiment ledger under `.foundry/hillclimb/<run-id>/state.json` containing
the selected environment, phase, approvals, operation IDs, receipt paths,
contract fingerprints, selected candidate, and final-test status. A schema
example is in [the contract template](../examples/project-contract.example.json).
Its unresolved fields must be filled; it is not runnable cloud configuration.

## Evaluation adapter

The project supplies an adapter that can:

1. Run a named configuration/model against a named dataset.
2. Reset task state between cases and use a fresh hosted session between
   independent cases. Reuse a session only within the intended conversation.
3. Collect every response's raw usage, requested and returned model identity,
   tool results, final output, actual latency, retry delays, and failures.
4. Evaluate quality with the frozen project evaluator, including agreed
   failure values. Do not discard failures or resample them until successful.
5. Emit one normalized episode record per planned case/rollout.

The cost tool accepts this JSONL shape:

```json
{
  "stage": "baseline",
  "episode_id": "case-a-run-1",
  "case_id": "case-a",
  "status": "success",
  "metrics": {"quality": 0.8},
  "latency_seconds": 1.2,
  "usage_complete": true,
  "calls": [{
    "pricing_key": "policy-default",
    "purpose": "inference",
    "context_input_tokens": 1200,
    "usage": {
      "uncached_input_tokens": 1000,
      "cached_input_tokens": 200,
      "cache_write_tokens": 0,
      "output_tokens": 100
    }
  }]
}
```

`usage` fields are normalized **charge quantities**, not necessarily all
physical token counts. Read the pricing reference for cache-write replacement
versus surcharge semantics. Retain raw SDK usage separately for auditing.
Missing counts are `null` or omitted and make cost incomplete; use zero only
when that bucket really had no chargeable tokens.

Use `status: "failed"` for genuine failures, retain observed calls, and set
`usage_complete: false` when requests or usage are unobserved. A confirmed
failure before any model/tool call may have zero cost with complete usage.
This flag describes completeness of the serving trajectory; evaluator and
optimizer calls are costed and checked separately by their call purpose.
Call purposes are `inference`, `evaluation`, or `optimizer`. Inference includes
all serving-time dependencies, such as lookup models; evaluation judges do
not belong in per-answer serving cost.

## Reproduction and fairness

Fingerprint UTF-8 text with CRLF normalized to LF, and hash actual resolved
instructions/tools, datasets, evaluator, model options, SDK/CLI versions, and
external-state versions. Do not store bearer tokens or API keys. Refuse to
resume when a fingerprint changes. Retargeting an existing deployment alias
or changing a live backend also requires a new run.

Balance rollouts between compared candidates and match the same cases/groups.
If an external step remains fixed, identify it separately; if inputs are
frozen during training, also evaluate the full live serving pipeline. Describe
stochastic and live-data limitations rather than promising identical scores.

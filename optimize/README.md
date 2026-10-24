# Agent Optimizer

Agent Optimizer changes instructions, skills, and tool definitions around the
base model. It does not change model weights.

Round one starts from `agents/<task>/baseline`. Round two starts from
`optimize/seeds/<task>/round2`. The search datasets are
`evals/datasets/optimization/<task>-round1.jsonl` and `...-round2.jsonl`.
The checked-in metadata names the model used for the published experiment;
change `model:` in a copied seed when running against another compatible
deployment.

Use `--agent-name` and `--experiment-name` to target a separately named hosted
agent and optimization job without modifying the published task names. For
example, `--agent-name mtas-product --experiment-name mtas-product-round1`.
Keep a copied seed with the intended model deployment and use
`--seed-dir`, `--dataset`, and `--name-suffix` to isolate local materialization.
When the separately named agent lives in a copied azd project, pass
`--project-dir` for that project's directory and `--project-endpoint` for the
existing Foundry project. `--no-wait` returns after submission; persist the
returned operation ID and monitor that job instead of submitting again.
Model selection is not requested: the generated configuration omits
`model_search_space` and retains the seed's policy model. Do not specify a
singleton containing the baseline model: the optimizer removes the baseline
from that list and rejects the resulting empty search space. The judge and
optimization models are separate dependencies, not candidate policy models.

```powershell
python optimize\run.py product `
  --round 1 `
  --agent-version <hosted-agent-version> `
  --eval-model <judge-deployment> `
  --optimization-model <optimizer-deployment> `
  --dry-run
```

Remove `--dry-run` only after reviewing the materialized configuration under
`optimize/work/`. Candidate output is reviewed and copied into
`agents/<task>/optimized` only after it improves the deterministic development
gate.

Current clean evidence:

| Task | Baseline | Optimized | Delta |
|---|---:|---:|---:|
| Product | 0.7529 | 0.9408 | +0.1880 |
| Shop | 0.7957 | 0.9371 | +0.1414 |
| Voucher | 0.8547 | 0.8606 | +0.0059 |
| Web | 0.6911 | 0.7286 | +0.0375 |

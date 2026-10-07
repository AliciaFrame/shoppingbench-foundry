# Agent Optimizer

Agent Optimizer changes instructions, skills, and tool definitions around the
base model. It does not change model weights.

Round one starts from `agents/<task>/baseline`. Round two starts from
`optimize/seeds/<task>/round2`. The search datasets are
`evals/datasets/optimization/<task>-round1.jsonl` and `...-round2.jsonl`.
The checked-in metadata names the model used for the published experiment;
change `model:` in a copied seed when running against another compatible
deployment.

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

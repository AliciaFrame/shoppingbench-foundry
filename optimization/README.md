# Agent Optimizer

Agent Optimizer changes the configuration surrounding the base model:

- instructions/developer prompt
- tool descriptions and parameter schemas
- task skills

It does **not** update model weights.

## Experiment design

Each task uses two disjoint optimization sets and one untouched canonical
holdout:

1. Run Agent Optimizer on round one.
2. Evaluate promising candidates on the 50-case canonical holdout.
3. Use the retained configuration as the round-two starting point.
4. Run round two on 40 new cases.
5. Retain the candidate only if the canonical holdout improves.

Run a task:

```powershell
python optimization\run.py product `
  --round 1 `
  --agent-version 1 `
  --eval-model <judge-deployment> `
  --optimization-model <optimizer-deployment>
```

Add `--dry-run` to materialize and inspect the optimizer configuration without
starting a job. Per-task PowerShell wrappers are under each task directory.

## Results

| Task | Outcome |
|---|---|
| Product | 0.753 → 0.895 → **0.961**; round two optimized tool definitions |
| Shop | 0.860 → **0.998**; round two optimized instructions and tools |
| Voucher | 0.898 → **0.955**; round-two candidate rejected after canonical regression |
| Web | **0.790** retained; generated candidates did not improve the canonical gate |

The raw receipts and machine-readable summary are under `results/`.

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
| Voucher | 0.898 → **0.955**; round-two candidate rejected, then RFT Step 12 reached **0.995** |
| Web | **0.790** retained; generated candidates did not improve the canonical gate |

The raw receipts and machine-readable summary are under `results/`.

## Post-RFT optimization

The selected Web RFT Step 10 deployment can be used as a new optimizer
baseline. Its isolated seed is under
`web/post-rft-step10-start`; it retains the original instructions, tools, and
knowledge-shopping skill while changing the model deployment to
`mai-sb-rft3-step10`.

```powershell
python optimization\run.py web `
  --round 2 `
  --agent-version 3 `
  --eval-model shoppingbench-eval-gpt-5-4-mini `
  --optimization-model shoppingbench-opt-gpt-5-4 `
  --seed-dir optimization\web\post-rft-step10-start `
  --dataset evaluations\datasets\web-optimize-round2.jsonl `
  --name-suffix post-rft-step10
```

The runner supports `--seed-dir`, `--dataset`, and `--name-suffix` so
post-training experiments remain separate from the original optimization
rounds. Candidate application and deployment remain explicit review gates.

Operation `opt_01bdafd831564fa69b18610f4594b8e2` completed all four
candidates. Candidate 1, a system-prompt mutation, won the optimizer judge at
`0.5885` versus `0.584625`, but failed the canonical gate:

| Artifact | Mean | Perfect | Exact | Terminated | Mean searches | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Step 10 + retained config | **0.901** | **43** | **44** | 47 | 6.24 | 49,321 |
| Step 10 + Candidate 1 | 0.871 | 40 | 41 | **49** | **4.64** | **44,904** |

The candidate improved efficiency and termination but regressed exact
selection, with three case wins and four losses. It was rejected and never
deployed. Receipts are
[`results/web-post-rft-step10-optimizer.json`](results/web-post-rft-step10-optimizer.json)
and
[`results/web-post-rft-step10-comparison.json`](results/web-post-rft-step10-comparison.json).

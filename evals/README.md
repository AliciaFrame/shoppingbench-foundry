# Evaluation

`evals/` owns the deterministic evaluation spine and all quality evidence.

## Layout

| Path | Role |
|---|---|
| `datasets/optimization/` | Agent Optimizer round-one and round-two search sets |
| `datasets/training/` | Group-disjoint pool available to RFT preparation |
| `datasets/development/` | 30-case checkpoint and promotion gate |
| `datasets/final/` | 50-case sealed final test |
| `datasets/manifest.json` | Split lineage, groups, and overlap assertions |
| `results/baseline/` | True off-the-shelf development receipts |
| `results/optimized/` | Retained optimized-agent receipts |
| `results/checkpoints/` | Voucher and hardened Catalog Web v5 checkpoint receipts |
| `results/comparisons/` | Paired, case-clustered comparisons |
| `results/final/` | One-time sealed final-test receipts |
| `results/summary.json` | Generated release metrics for score, latency, and tokens |

## Grading

The shared grader in `shoppingbench_foundry.grading` scores exact ordered
selection, task constraints, tool process, and grounded output. Agent
Optimizer may use `builtin.task_adherence` to search candidates, but only this
deterministic grader controls promotion.

The grader implementations live in the installable
`shoppingbench_foundry` package because both local evaluation and the deployed
API use the same code.

Live Web Search uses the same canonical grader after its native web-search
phase. Its baseline and retained optimized receipts are
`results/baseline/web-search.jsonl` and
`results/optimized/web-search.jsonl`. RFT results are intentionally not
published while that experiment remains work in progress.

## Evaluate

```powershell
$env:SHOPPINGBENCH_TASK = "product"
$env:OPTIMIZATION_LOCAL_DIR = "product/baseline"
python evals\scripts\evaluate_agent.py `
  --runtime-module agents.shared.baseline_runtime `
  --dataset evals\datasets\development\product.jsonl `
  --documents data\prepared\search-documents.jsonl `
  --output .foundry\results\product-baseline.jsonl `
  --rollouts 3 `
  --workers 1
```

For the optimized condition, use `product/optimized` and
`agents.shared.runtime`. Summarize a paired comparison with:

```powershell
python evals\scripts\summarize_development.py `
  --baseline <baseline-receipt> `
  --candidate <candidate-receipt> `
  --output <comparison.json>
```

Regenerate the release summary with:

```powershell
python evals\scripts\build_release_summary.py
```

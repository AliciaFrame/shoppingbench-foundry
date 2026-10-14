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
| `results/checkpoints/` | Voucher, Web (static) v5, and Web (live) v6 checkpoint receipts |
| `results/scored/` | Web (live) RFT-reward scores, summaries, and paired comparisons |
| `results/comparisons/` | Paired, case-clustered comparisons |
| `results/final/` | One-time sealed final-test receipts |
| `results/summary.json` | Generated release metrics for score, latency, and tokens |
| `results/model-comparison/` | Portable GPT score/usage receipts, prices and provenance, and generated MAI/GPT cost comparison |

## Grading

The shared grader in `shoppingbench_foundry.grading` scores exact ordered
selection, task constraints, tool process, and grounded output. Agent
Optimizer may use `builtin.task_adherence` to search candidates, but only this
deterministic grader controls promotion.

The grader implementations live in the installable
`shoppingbench_foundry` package because both local evaluation and the deployed
API use the same code.

Web (live) uses the same canonical grader after its web-search lookup.
`scripts/evaluate_live_web.py` produces its receipts and
`scripts/score_live_web.py` rescores them with the Web (live) RFT reward.
Receipts are `results/{baseline,optimized,final}/web-search.jsonl` and
`results/checkpoints/web-search-v6/final.jsonl`.

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
`agents.shared.runtime`. Both conditions persist failed episodes with zero
score and keep observed tokens and latency. Failure exit status is nonzero
unless `--record-errors-as-zero` is supplied. A manifest prevents resuming
under a different model, configuration, dataset, or catalog; legacy outputs
without manifests require a new output path.

Summarize a paired comparison with:

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

## Reproduce the model cost comparison

```powershell
python evals\scripts\compare_model_costs.py --write-readme
python evals\scripts\compare_model_costs.py --check
```

This requires only Python's standard library, not Azure or inference. The
manifest references existing MAI receipts and compact GPT-5.6 receipts for
every complete vanilla and agent-optimized candidate. Compact receipts retain
episode names, canonical scores, aggregate token usage, failure flags, and
per-call usage by model stage for Web (live); they are not full trajectories
and cannot be rescored. Source-file hashes record their provenance. Dataset
and committed-receipt hashes are verified with CRLF normalized to LF before
calculation, along with the exact case/rollout names and usage totals.

The script prices input/output tokens at each model's manifest rates, with
all input uncached and the Web (live) lookup priced separately. It selects
the highest mean canonical score among complete GPT runs, retaining failures
as zero. Costs use recorded token usage, excluding unavailable usage from
failed episodes. The cost badge marks the lowest recorded cost in each row.
Missing-usage counts and lower-bound flags remain available in the JSON summary.

`results/model-comparison/summary.json` preserves metrics for both candidate
stages, not just the selected stage: total input/output tokens, scores, costs,
errors, and missing-usage counts. Two incomplete candidates are documented
in the manifest and excluded, not silently treated as full runs.

For new receipts, edit the manifest's relative input paths, SHA-256 hashes,
and prices, then regenerate. This reproduces the saved-run arithmetic, not
the original inference or training spend. Use `scripts/evaluate_agent.py`
or `scripts/evaluate_live_web.py` for new inference; choose the deployment
with `MAI_MODEL_DEPLOYMENT_NAME` and keep the same dataset and baseline/optimized
configuration. For Web (live), retain the fixed lookup deployment in
`WEB_SEARCH_MODEL_DEPLOYMENT_NAME` when changing the selection model.

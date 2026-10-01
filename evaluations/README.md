# Evaluations and graders

This directory centralizes the evidence used to make deployment decisions.

## Why deterministic grading

Agent Optimizer uses `builtin.task_adherence` to search the configuration
space. That score is useful for candidate generation, but it is not the final
deployment gate. Shopping tasks have exact invariants that can be scored
directly:

- ordered product IDs
- title/price/service/SKU constraints
- same-shop membership
- voucher arithmetic and budget
- knowledge attribute match
- recommendation and termination protocol

`shoppingbench_foundry.grading` implements the canonical grader used for local
holdouts and the deployed `/grade` endpoint. `graders/rft_grader.py` provides a
self-contained reward implementation for RFT calibration.

## Dataset roles

| File pattern | Role |
|---|---|
| `*-optimize.jsonl` | Round-one optimizer search |
| `*-optimize-round2.jsonl` | Disjoint round-two optimizer search |
| `*-holdout.jsonl` | Untouched 50-case canonical deployment gate |

## Run a canonical evaluation

Set the selected task and configuration, then:

```powershell
$env:SHOPPINGBENCH_TASK = "product"
$env:OPTIMIZATION_LOCAL_DIR = "agents/product/config"
python evaluations\scripts\evaluate_agent.py `
  --dataset evaluations\datasets\product-holdout.jsonl `
  --documents data\prepared\search-documents.jsonl `
  --output .foundry\results\product-heldout.jsonl `
  --workers 1
```

Use one worker by default when the external model deployment has a constrained
token-rate limit.

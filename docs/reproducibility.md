# Reproducing the demo

This repository supports two reproducibility levels:

1. **Evidence verification** reproduces every deterministic dataset, split, and
   published result calculation without Azure access.
2. **Cloud reruns** redeploy the tools and agents, rerun model episodes, launch
   Agent Optimizer, and submit RFT jobs. Model sampling and preview services are
   not bit-for-bit deterministic, so compare canonical metrics rather than raw
   response text.

## Prerequisites

- Python 3.11 or newer
- Azure CLI and Azure Developer CLI 1.27.1 or newer
- An Azure subscription where you can create the resources in `azure/infra`
- A Microsoft Foundry project with a MAI-Code deployment
- Foundry Agent Optimizer access for optimization reruns
- Reinforcement fine-tuning access for RFT reruns

Use `MAI-Code-1.1-Flash-2026-09-15` for the agent and Agent Optimizer
experiments documented in this repository. The current Web RFT experiment uses
`mai-code-1.1-flash-2026-08-27`, the checkpoint that advertises fine-tuning
capability in Sweden Central.

## Verify the published evidence

From a clean clone:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agents,dev]"
.\.venv\Scripts\python data\scripts\prepare.py
.\.venv\Scripts\python rft\scripts\prepare_data.py
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

Expected deterministic outputs:

- 900 source cases
- 3,636 search documents
- 20 round-one, 40 round-two, and 50 holdout cases per task
- RFT train/validation rows:
  - Product, Shop, Voucher: 180/20
  - Web: 80/20
- all canonical holdouts excluded from RFT data
- documented optimization metrics matching the raw receipts

## Deploy the tools

Copy `.env.example` to `.env`, generate a bearer token of at least 32
characters, then follow `azure/README.md`.

The API fails closed when `SHOPPINGBENCH_API_TOKEN` is absent. Set
`SHOPPINGBENCH_ALLOW_ANONYMOUS=true` only for an explicitly isolated local
development process.

After provisioning:

1. Grant the indexing identity `Search Index Data Contributor`.
2. Load `data/prepared/search-documents.jsonl`.
3. Confirm `/health` returns `{"status":"ok"}`.
4. Confirm protected tool requests return `401` without the bearer token and
   succeed with it.

## Deploy and evaluate agents

Set the model, project, and tool variables described in `.env.example`, then
deploy from `agents/`.

For final retained behavior, each agent uses `agents/<task>/config`. To rerun a
baseline, set `OPTIMIZATION_LOCAL_DIR` to
`optimization/<task>/baseline` before invoking the evaluator. Round-two seeds
are under `optimization/<task>/round2-start`.

Run canonical evaluation with the unchanged 50-case holdout:

```powershell
$env:SHOPPINGBENCH_TASK = "product"
$env:OPTIMIZATION_LOCAL_DIR = "agents/product/config"
python evaluations\scripts\evaluate_agent.py `
  --dataset evaluations\datasets\product-holdout.jsonl `
  --documents data\prepared\search-documents.jsonl `
  --output .foundry\results\product-heldout.jsonl `
  --workers 1
```

Repeat with `shop`, `voucher`, and `web`. Compare the generated mean, perfect
count, success count, termination count, and token usage with
`optimization/results/summary.json` and the raw receipts.

## Rerun Agent Optimizer

Deploy the matching hosted agent first, then run the command in
`optimization/README.md`. Use the provided round-specific dataset and seed
configuration. Optimizer judge scores nominate candidates; always rerun the
canonical holdout before retaining one.

## Rerun RFT

Follow `rft/README.md` in this order:

1. regenerate holdout-safe data
2. collect base-model validation rollouts
3. calibrate the grader
4. verify a 25–50% base failure rate
5. submit one task-specific job
6. evaluate every available checkpoint on the untouched canonical holdout

The Web configuration uses one endpoint grader, four authenticated live tools,
a calibrated pass threshold of `0.9`, and `max_episode_steps=12`.

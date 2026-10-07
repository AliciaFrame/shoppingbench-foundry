# ShoppingBench agent hardening on Microsoft Foundry

[![CI](https://github.com/AliciaFrame/shoppingbench-foundry/actions/workflows/ci.yml/badge.svg)](https://github.com/AliciaFrame/shoppingbench-foundry/actions/workflows/ci.yml)

This repository is a reproducible end-to-end demonstration of improving
tool-using shopping agents on Microsoft Foundry.

ShoppingBench evaluates whether an agent can search a product catalog, inspect
evidence, recommend the correct product IDs, terminate cleanly, and return a
grounded user-facing answer. The included tasks cover:

- **Product:** select one item satisfying all constraints.
- **Shop:** select multiple requested items from the same shop.
- **Voucher:** apply shop scope, threshold, discount, and budget arithmetic.
- **Catalog Web:** resolve a factual clue from model knowledge, then select the
  matching catalog product.
- **Live Web Search:** resolve the clue with native Responses API web search,
  then select the matching catalog product.

## What we did

Each task follows one evaluation spine:

1. build leakage-safe, connected-group-disjoint datasets;
2. measure an off-the-shelf baseline;
3. run Agent Optimizer without changing model weights;
4. use reinforcement fine-tuning only when calibration shows useful signal;
5. evaluate every retained configuration on the same deterministic grader;
6. report score, latency, and token cost together.

Agent Optimizer produced the retained Product, Shop, and Live Web Search
agents. Voucher improved further with RFT Step 10. Catalog Web uses the
hardened v5 reward and its selected final checkpoint. Live Web Search RFT is
still work in progress and is intentionally excluded from the published RFT
configuration and results.

## Final development results

Every stage below uses the same 30-case development split for its task, three
rollouts per case, and 90 total episodes. Values are mean deterministic score,
seconds per episode, and total tokens per episode.

| Task | Stage | Mean score | Latency | Tokens |
|---|---|---:|---:|---:|
| **Product** | Base | 0.7529 | 31.80s | 16,227 |
|  | **Agent Optimized** | **0.9408** | **16.22s** | **13,948** |
|  | RFT | — | — | — |
| **Shop** | Base | 0.7957 | 51.31s | **14,821** |
|  | **Agent Optimized** | **0.9371** | **25.56s** | 33,799 |
|  | RFT | — | — | — |
| **Voucher** | Base | 0.8547 | 61.75s | **20,892** |
|  | Agent Optimized | 0.8606 | 35.17s | 30,897 |
|  | **RFT Step 10** | **0.9248** | **23.10s** | 29,610 |
| **Catalog Web** | Base | 0.6911 | 99.74s | 77,709 |
|  | Agent Optimized | **0.7286** | 100.97s | 97,156 |
|  | **Hardened v5 RFT final** | 0.7175 | **48.75s** | **37,278** |
| **Live Web Search** | Base | 0.7636 | **13.77s** | **13,586** |
|  | **Agent Optimized** | **0.9047** | 15.23s | 17,779 |
|  | RFT | **WIP** | **WIP** | **WIP** |

### Selected result versus base

| Task | Selected stage | Score gain | Latency change | Token change |
|---|---|---:|---:|---:|
| Product | Agent Optimized | **+0.1880** | **-49.0%** | **-14.0%** |
| Shop | Agent Optimized | **+0.1414** | **-50.2%** | +128.0% |
| Voucher | RFT Step 10 | **+0.0700** | **-62.6%** | +41.7% |
| Catalog Web | Hardened v5 RFT final | **+0.0264** | **-51.1%** | **-52.0%** |
| Live Web Search | Agent Optimized | **+0.1411** | +10.6% | +30.9% |

The machine-readable source for these tables is
[`evals/results/summary.json`](evals/results/summary.json), generated from the
committed JSONL receipts by
[`evals/scripts/build_release_summary.py`](evals/scripts/build_release_summary.py).

## Repository map

| Directory | Purpose |
|---|---|
| [`agents`](agents) | Runnable baseline and optimized agents, including the native live-web-search runtime |
| [`data`](data) | Frozen ShoppingBench rows and deterministic catalog generation |
| [`evals`](evals) | Lifecycle datasets, evaluation scripts, receipts, comparisons, and release summary |
| [`optimize`](optimize) | Agent Optimizer runner and reproducible seed configurations |
| [`rft`](rft) | Voucher and hardened Catalog Web data preparation, calibration, submission, and job receipts |
| [`src/shoppingbench_foundry`](src/shoppingbench_foundry) | Tool API, canonical grader, RFT graders, and catalog store |
| [`azure`](azure) | Azure deployment infrastructure for the tool and grader API |
| [`docs`](docs/README.md) | Architecture, results, reproducibility, and demo walkthrough |
| [`CHANGELOG.md`](CHANGELOG.md) | Superseded experiments and methodology changes |

## Reproduce locally

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agents,dev]"
.\.venv\Scripts\python data\scripts\prepare.py
.\.venv\Scripts\python rft\scripts\prepare_data.py
.\.venv\Scripts\python evals\scripts\build_release_summary.py
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

Live evaluation additionally requires a compatible Responses API deployment,
the ShoppingBench tool API, and the environment variables documented in
[`.env.example`](.env.example). See
[`docs/reproducibility.md`](docs/reproducibility.md) for the full baseline,
Agent Optimizer, RFT, and comparison commands.

## Azure and Foundry

The Azure layer deploys the searchable catalog and authenticated tool/grader
API. Hosted agents are deployed separately through the Microsoft Foundry
agent manifest under [`agents/azure.yaml`](agents/azure.yaml).

RFT is optional, access-dependent, and billable. Published Voucher and Catalog
Web receipts are under [`rft/jobs`](rft/jobs/README.md). Job, file, model, and
checkpoint IDs are evidence from the original runs and must not be reused as
configuration for a new environment.

## Attribution

This project adapts [ShoppingBench](https://github.com/yjwjy/ShoppingBench)
under the Apache License 2.0. See [NOTICE](NOTICE).

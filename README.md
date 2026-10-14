# ShoppingBench agent hill climbing on Microsoft Foundry

[![CI](https://github.com/AliciaFrame/shoppingbench-foundry/actions/workflows/ci.yml/badge.svg)](https://github.com/AliciaFrame/shoppingbench-foundry/actions/workflows/ci.yml)

This repository is a reproducible, end-to-end demonstration of improving five
tool-using shopping agents built on **MAI-Code-1.1-Flash**, using two levers
in sequence:

1. **Agent Optimizer** rewrites instructions, skills, and tool descriptions
   without changing model weights.
2. **Reinforcement fine-tuning (RFT)** changes the weights, and is used only
   where calibration shows a learnable signal.

ShoppingBench evaluates whether an agent can search a product catalog, inspect
evidence, recommend the correct product IDs, terminate cleanly, and return a
grounded answer. The five agents are:

| Agent | Task |
|---|---|
| **Product** | Select one item satisfying all constraints. |
| **Shop** | Select multiple requested items from the same shop. |
| **Voucher** | Apply shop scope, threshold, discount, and budget arithmetic. |
| **Web (static)** | Resolve a factual clue from model knowledge, then select the matching product. |
| **Web (live)** | Resolve the clue with live web search, then select the matching product. |

## Results

Every stage uses the same 30-case development split for its task with three
rollouts per case (90 episodes). Score is the canonical deterministic
ShoppingBench score; runtime errors count as zero. Cost is per 1,000 episodes
at list price (MAI-Code-1.1-Flash: $0.20 input / $1.20 output per 1M tokens,
input priced uncached).

| Agent | Stage | Score | Latency | Tokens | Cost / 1k episodes |
|---|---|---:|---:|---:|---:|
| **Product** | Base | 0.753 | 31.8s | 16,227 | $5.37 |
|  | **Agent Optimized** ✅ | **0.941** | **16.2s** | **13,948** | $5.64 |
| **Shop** | Base | 0.796 | 51.3s | 14,821 | $6.49 |
|  | **Agent Optimized** ✅ | **0.937** | **25.6s** | 33,799 | $11.98 |
| **Voucher** | Base | 0.855 | 61.7s | 20,892 | $9.44 |
|  | Agent Optimized | 0.861 | 35.2s | 30,897 | $12.08 |
|  | **RFT** ✅ | **0.925** | **23.1s** | 29,610 | **$8.90** |
| **Web (static)** | Base | 0.691 | 99.7s | 77,709 | $31.71 |
|  | **Agent Optimized** ✅ | **0.729** | 101.0s | 97,156 | $38.53 |
|  | RFT (token alternative) | 0.718 | **48.8s** | **≥ 37,278** | **≥ $13.33** |
| **Web (live)** | Base | 0.250 | 39.2s | 24,023 | $16.79 |
|  | Agent Optimized | 0.829 | 20.0s | 25,236 | $13.19 |
|  | **RFT** ✅ | **0.903** | **19.8s** | 26,198 | **$12.56** |

✅ marks the selected configuration. Product and Shop reached their results
with Agent Optimizer alone, so RFT was not needed.

**Web (static)** offers two choices: Agent Optimized has the best score, while
its RFT checkpoint gives up 0.011 score with lower recorded token consumption
and latency. Twelve RFT failures lack usage, so its token and cost figures
are lower bounds, not exact totals.

**Web (live)** climbs at both steps, and both gains are statistically clear
(paired, case-clustered 95% confidence intervals):

| Step | Score gain | 95% CI | RFT reward | Exact product |
|---|---:|---|---:|---:|
| Base → Agent Optimized | +0.579 | [+0.437, +0.711] | 0.180 → 0.739 | 21 → 74 / 90 |
| Agent Optimized → RFT | +0.073 | [+0.001, +0.165] | 0.739 → 0.820 | 74 → 81 / 90 |

RFT roughly halved the episodes that ran out of tool steps without
recommending a product (15 → 7). On the sealed 50-case final test, opened once
after selection, the RFT agent scored **0.906** with 45/50 exact products.
Web (live) cost includes the GPT-5.4-mini web-search lookup that precedes the
agent, priced at $0.75 / $4.50 per 1M tokens.

The machine-readable source for these tables is
[`evals/results/summary.json`](evals/results/summary.json), generated from the
committed per-episode receipts by
[`evals/scripts/build_release_summary.py`](evals/scripts/build_release_summary.py).
See [`docs/results.md`](docs/results.md) for interpretation.

<!-- model-cost-comparison:start -->
## Cost comparison with GPT-5.6

We compare the published MAI-Code-1.1-Flash choices against each GPT-5.6
model's highest observed canonical score among available complete vanilla
and agent-optimized runs. Each run uses the same 30 development cases
and 3 rollouts (90 episodes); failed episodes remain zero-scored.
The two Web (static) rows show the accuracy choice and the RFT token alternative.

| MAI run | MAI score | MAI cost | Luna score | Luna cost | Terra score | Terra cost | Sol score | Sol cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Product - optimized | **0.941 🏆** | $5.64 | 0.841\* | ≥ $3.21 | 0.766\* | ≥ $19.15 | 0.865\* | ≥ $47.16 |
| Shop - optimized | **0.937 🏆** | $11.98 | 0.890 | **$9.06 💰** | 0.833\* | ≥ $59.16 | 0.871 | ≥ $163.19 |
| Voucher - RFT, step 10 | **0.925 🏆** | **$8.90 💰** | 0.705 | $19.36 | 0.742 | $185.01 | 0.781 | ≥ $374.95 |
| Web (static) - optimized | 0.729 | $38.53 | 0.703 | **$13.95 💰** | 0.823 | $91.69 | **0.868 🏆** | ≥ $356.65 |
| Web (static) - RFT v5, token alternative | 0.718 | ≥ $13.33 | 0.703 | $13.95 | 0.823 | $91.69 | **0.868 🏆** | ≥ $356.65 |
| Web (live) - RFT v6, final | 0.903 | $12.56 | **0.968\* 🏆** | **$9.69 💰** | 0.946\* | $30.96 | 0.941\* | $55.61 |

**🏆** marks the highest measured score in each row (not a claim of
statistical significance). **💰** marks the provably cheapest token cost
among that row's runs. If an unknown cost could be cheaper, no cheapest badge
is awarded. **\*** marks a selected agent-optimized GPT run; unmarked GPT
results are vanilla. These GPT runs use the MAI-tuned configuration, not
independent GPT-specific tuning. Selection is among available complete runs,
not every possible optimized configuration.
Excluded incomplete candidates: Luna Voucher optimized (47/90); Terra Web (static) optimized (85/90).

Costs are **USD per 1,000 episodes**, calculated from actual API-reported
input and output tokens, including reasoning output, with all input priced
uncached. Rates per 1M tokens (input / output):
MAI $0.20 / $1.20;
Luna $0.20 / $1.20;
Terra $2.00 / $12.00;
Sol $4.00 / $20.00.
Web (live) also includes the GPT-5.4-mini lookup at
$0.75 / $4.50.
Training, hosting, and any separately billed search/tool fees are excluded.

**≥ means a lower bound, not an exact cost:** some failed episodes lack
usage records. Their scores still count as zero, but their missing tokens
cannot be priced. A smaller recorded cost with missing usage is not proof
that a run is cheaper than another run with complete usage.

Recalculate the table offline, without Azure credentials or inference:

```powershell
python evals\scripts\compare_model_costs.py --write-readme
python evals\scripts\compare_model_costs.py --check
```

The [manifest](evals/results/model-comparison/manifest.json) contains prices,
all eligible stage inputs, excluded-run counts, and SHA-256 provenance.
Receipt and dataset hashes normalize CRLF to LF for cross-platform checks.
Committed GPT receipts retain per-episode scores, token usage, and failure
flags; MAI runs reference the existing full receipts. The
[generated summary](evals/results/model-comparison/summary.json) includes
every candidate's score, total tokens, failure count, and missing-usage count.
This reproduces the arithmetic and stage selection, not a new inference run.
<!-- model-cost-comparison:end -->

## Repository map

| Directory | Purpose |
|---|---|
| [`agents`](agents) | Runnable baseline and optimized agents for all five tasks |
| [`data`](data) | Frozen ShoppingBench rows and deterministic catalog generation |
| [`evals`](evals) | Datasets, evaluation and scoring scripts, receipts, comparisons, and release summary |
| [`optimize`](optimize) | Agent Optimizer runner and reproducible seed configurations |
| [`rft`](rft) | RFT data preparation, calibration, submission, and job receipts |
| [`src/shoppingbench_foundry`](src/shoppingbench_foundry) | Tool API, canonical grader, RFT graders, and catalog store |
| [`azure`](azure) | Azure deployment for the catalog, tool API, and RFT grader endpoints |
| [`docs`](docs/README.md) | Architecture, results, reproducibility, and demo walkthrough |

## Reproduce

Deterministic artifacts, tests, and the release tables reproduce locally:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agents,dev]"
.\.venv\Scripts\python data\scripts\prepare.py
.\.venv\Scripts\python rft\scripts\prepare_data.py
.\.venv\Scripts\python evals\scripts\build_release_summary.py
.\.venv\Scripts\python evals\scripts\compare_model_costs.py --check
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

Rerunning the agents requires a MAI-Code-1.1-Flash deployment, the deployed
ShoppingBench tool API, and the environment variables in
[`.env.example`](.env.example). [`docs/reproducibility.md`](docs/reproducibility.md)
gives the exact commands for every row above: base and optimized evaluation,
Agent Optimizer, RFT calibration and submission, checkpoint evaluation, and
scoring.

RFT is optional, access-dependent, and billable. Job receipts under
[`rft/jobs`](rft/jobs/README.md) record the original runs; their IDs are
evidence and must not be reused as configuration.

## Attribution

This project adapts [ShoppingBench](https://github.com/yjwjy/ShoppingBench)
under the Apache License 2.0. See [NOTICE](NOTICE).

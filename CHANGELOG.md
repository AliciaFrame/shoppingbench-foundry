# Methodology changelog

This file records material changes to experiment design and interpretation.
The active workflow and published evidence are documented in the root README
and `docs/results.md`.

## 2026-10-07 - Hardened v2 release results

The active repository view now contains one final result set covering Product,
Shop, Voucher, hardened Catalog Web v5, and Live Web Search.

- Product and Shop publish their retained Agent Optimizer configurations.
- Voucher publishes RFT Step 10.
- Catalog Web publishes the strict v5 grader, datasets, selected final
  checkpoint, and independent development evaluation.
- Live Web Search publishes the clean baseline and retained contract-correct
  Agent Optimizer result.
- Live Web Search RFT remains work in progress. Its data profile, grader,
  submission code, and job receipts are intentionally excluded from the
  published repository until it produces a result worth retaining.

The root README now reports one 90-episode development comparison for each
stage, including mean score, latency, and token use. The tables are generated
from `evals/results/summary.json`; historical experiment narratives remain in
this changelog rather than the active README.

Superseded catalog-Web RFT artifacts were removed from the active lifecycle.
The retained Web experiment uses the v5 hard gates for exact product, resolved
clue, grounded answer, and completed protocol. Historical v4 evidence remains
useful for explaining why those gates were added, but it is not presented as
the current result.

## 2026-10-05 - Leakage-safe end-to-end experiment

The repository now uses one reproducible lifecycle:

- connected-group-disjoint data generation;
- a true off-the-shelf baseline runtime;
- retained Agent Optimizer configurations;
- current deterministic grading;
- v3 RFT calibration, jobs, and checkpoint evaluation;
- sealed final-test receipts.

Canonical development results are:

| Task | Off-the-shelf | Optimized | RFT decision |
|---|---:|---:|---|
| Product | 0.7529 | 0.9408 | Not trained |
| Shop | 0.7957 | 0.9371 | Not trained |
| Voucher | 0.8547 | 0.8606 | Step 10 selected at 0.9248 |
| Web | 0.6911 | 0.7286 | All current checkpoints rejected |

### Baseline definition correction

An earlier draft evaluated the minimal prompt configuration through the
optimized runtime. That runtime forced sequential tool use, hid
`terminate` until recommendation, required recommendation followed by
termination, and injected a grounded final-answer request. Those scores were
configuration ablations rather than off-the-shelf baselines.

`agents/shared/baseline_runtime.py` restores the original unassisted loop so
the first stage now measures base-model behavior rather than runtime help.

### Split and evaluation correction

The earlier methodology reused evaluation rows for candidate and checkpoint
selection, so those rows were development data rather than a sealed final
test. Web also had target-product overlap between training and evaluation. The
updated methodology:

- groups cases by target product and normalized Web answer;
- assigns each connected group wholly to training, development, or final test;
- uses development for optimization and checkpoint decisions;
- opens the sealed final test only after choices are frozen;
- requires grounded user-facing answers;
- reports strict success and component metrics alongside weighted means.

Earlier aggregate claims are not directly comparable to the current
experiment and are not presented as active results.

### Findings retained from earlier runs

The historical Web `/grade/v2` RFT job showed that an early checkpoint could
beat the final checkpoint. That finding motivated mandatory checkpoint
evaluation. It is not used as the active Web RFT result because it used a
different reward and lineage.

A later post-RFT Agent Optimizer candidate improved the LLM judge and reduced
tokens but reduced deterministic exact selection. It was rejected, reinforcing
the rule that optimizer scores do not control deployment.

The current Web job provides the comparable result: every checkpoint
underperformed the optimized base model, so the optimized model was retained.

### Repository organization

The repository moved from experiment-specific folders to lifecycle ownership:

- `evaluations/` -> `evals/`
- `optimization/` -> `optimize/`
- agent configurations -> `agents/<task>/baseline` and `optimized`
- quality receipts -> `evals/results`
- RFT train/validation rows -> `rft/data`
- immutable job receipts -> `rft/jobs`

Superseded datasets, regrades, smoke runs, optimizer raw dumps, submission
wrappers, and audit files are excluded from the active workflow.

## 2026-10-06 - Live Web search remediation

A separate `web_search` lineage now resolves factual clues with the native
Responses API web-search tool before selecting a product from the catalog.
This keeps the internet-enabled experiment distinct from the catalog-only Web
RFT lineage.

The clean GPT-5.4-mini development baseline and retained Agent Optimizer
configuration were evaluated on the same 30 cases with three rollouts each:

| Stage | Mean | Exact | Success | Valid final |
|---|---:|---:|---:|---:|
| Live-search baseline | 0.7636 | 75/90 | 13/90 | 33/90 |
| Raw optimizer candidate | 0.7664 | 80/90 | 0/90 | 0/90 |
| Contract-fixed optimizer candidate | **0.9047** | **80/90** | **78/90** | **83/90** |

The raw optimizer candidate reversed the terminal schema semantics and put only
the clue in `final_answer`. It was rejected. Restoring the invariant that
`resolved_clue` contains the factual resolution and `final_answer` contains the
complete grounded recommendation retained the search improvements and produced
the verified optimized result.

The retained configuration was deployed as hosted agent version 6 and verified
with a live invocation that resolved Alec Aitken to `violin`, selected product
`3706669986`, and returned the complete grounded terminal answer.

The catalog-only Web v4 calibration used 20 independent cases with five
rollouts each. Runtime failures are now retained as explicit zero-score
episodes rather than silently omitted. The calibrated mean was `0.6116`, with
a 28% failure rate at the recommended `0.50` threshold and sufficient signal.

No new RFT job could be submitted. On the approved Sweden Central resource,
the active `mai-code-1.1-flash` alias resolves to `2026-09-15`, which the
fine-tuning service currently rejects in that region. The former
`2026-08-27` version now fails because its Blossom recipe is no longer valid.
The experiment remains pinned to the approved ShoppingBench resource, so the
calibration is retained and training is recorded as platform-blocked.

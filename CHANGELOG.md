# Changelog

## Workflow versions: v1 versus v2

**v1** and **v2** describe repository and evaluation workflow generations,
not Python package release numbers. The published Web (static) **v5** and
Web (live) **v6** RFT graders are task-specific revisions, not successive
versions of the repository; both are used in v2.

| Area | Earlier v1 workflow | Hardened v2 workflow |
|---|---|---|
| Agent scenarios | Product, Shop, Voucher, and Web | Product, Shop, Voucher, Web (static), and Web (live) |
| Candidate selection | 50-case canonical holdouts used across candidate and checkpoint comparisons | 30 development cases, three rollouts each; 50-case sealed final test opened once after selection |
| Baselines | Experiment-specific initial and retained agent configurations | Explicit off-the-shelf runtime, separate from optimized orchestration |
| Data lifecycle | Earlier experiment-specific subsets | Connected-group-disjoint optimizer, RFT, development, and final splits |
| Evidence | Experiment receipts and retained checkpoint comparisons | Uniform canonical stage metrics, case-clustered confidence intervals, and reproducible recorded token costs |
| Repository layout | `evaluations/` and `optimization/` | `evals/` and `optimize/`, with published stage and checkpoint receipts |

**Do not compare v1 and v2 scores as a single improvement curve.** Evaluation
cohorts, baseline orchestration, and grading/protocol requirements changed.
Use the matched base, Agent Optimized, and RFT comparisons within v2 instead.

## 2026-10-14 - Cost comparison and reproducibility hardening

- Added MAI versus GPT-5.6 Luna, Terra, and Sol comparisons, selecting the best
  complete vanilla or agent-optimized GPT run. Both Web (static) MAI choices
  remain visible. The compact table marks highest scores and lowest recorded
  token costs.
- Added portable score/usage inputs, prices, provenance, and
  `evals/scripts/compare_model_costs.py` to reproduce the comparison offline.
- Catalog evaluation now retains failed episodes as zero scores with observed
  usage and latency. Run manifests bind resumptions to the model, configuration,
  dataset, and catalog; transport retries no longer restart whole episodes.
- Live Web scoring now validates receipts and paired baseline inputs before
  judge calls. The guide scores the baseline first, and cached scores are bound
  to the dataset, grader, and configured judge.
- Normalized text fingerprints to LF, with compatibility for historical
  LF/CRLF semantic-review hashes. Derived data regenerate without platform
  drift; original job and evaluation receipts remain unchanged.
- Made Windows agent vendoring idempotent, enabled CI on the v2 branch, and
  added curated Live Web data regeneration to CI checks.
- Restored benchmark, architecture, corpus, and setup explainers in the README.
  Updated the slide descriptions to use recorded token cost consistently.
- The reproducibility fixes leave published scores and recorded costs unchanged
  and required no additional training or benchmark inference.

## 2026-10-13 - Five-agent hill climb on MAI-Code-1.1-Flash

- All five agents (Product, Shop, Voucher, Web (static), Web (live)) now run on
  MAI-Code-1.1-Flash and report base, Agent Optimized, and RFT stages on one
  evaluation spine.
- Web (live) publishes its full hill climb: 0.250 → 0.829 → 0.903 canonical
  score, with both steps statistically clear, and 0.906 on the sealed final
  test.
- Added the Web (live) RFT reward (`/grade/web-search/v6`): semantic LLM
  judging of clue correctness and grounding, deterministic product and
  protocol gates, and a succinctness cost.
- Added `evals/scripts/evaluate_live_web.py` and
  `evals/scripts/score_live_web.py`, the Web (live) RFT data and captures under
  `rft/data/web-search/`, and job receipts under `rft/jobs/web-search-v6/`.
- The release summary now reports list-price cost per 1,000 episodes.
- Web (static) selects its Agent Optimized configuration and publishes the RFT
  checkpoint as a lower-recorded-token alternative.
- Grader endpoints retry only unreachable or throttled judge calls, never a
  returned judgment.

## 2026-10-07 - Hardened release

- Product and Shop publish their Agent Optimizer configurations.
- Voucher publishes RFT Step 10.
- Web (static) publishes the strict v5 grader, datasets, selected checkpoint,
  and independent development evaluation.
- Release tables are generated from `evals/results/summary.json`.

## 2026-10-05 - Leakage-safe lifecycle

- Connected-group-disjoint optimization, training, development, and sealed
  final-test splits.
- A true off-the-shelf baseline runtime (`agents/shared/baseline_runtime.py`)
  that gives the model no orchestration help.
- Deterministic canonical grading with explicit zeroes for errors and
  nontermination, and case-clustered confidence intervals.

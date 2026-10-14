# Changelog

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
  checkpoint as a lower-cost alternative.
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

# Reinforcement fine-tuning

RFT changes model behavior through live multi-step catalog-tool episodes. It
starts from the retained optimized agent configuration and uses deterministic
endpoint graders hosted by the ShoppingBench API.

The published RFT workflow covers:

- **Voucher**, using the canonical v3 reward;
- **Web (static)**, using the hardened v5 clue and grounding reward;
- **Web (live)**, using the semantic web-search reward
  (`web_search_rft_grading_v6`).

Product and Shop were not trained: Agent Optimizer already reached 0.94, and
calibration did not show enough useful base-model failures.

## Workflow

1. Generate connected-group-disjoint train and validation rows.
2. Run repeated base-model calibration episodes.
3. Select a threshold with a 25-50% base failure rate.
4. Train only when calibration has sufficient samples and signal.
5. Deploy and evaluate every retained checkpoint on the development split.
6. Select a checkpoint using quality, latency, tokens, and strict completion.

Generate the published RFT datasets:

```powershell
python rft\scripts\prepare_data.py
```

Each row contains the optimized developer message and the catalog tool schemas.

## Voucher

Calibrate:

```powershell
python -m rft.scripts.calibrate `
  --results <voucher-calibration-results.jsonl> `
  --dataset rft\data\voucher-calibration-eval.jsonl `
  --output .foundry\voucher-calibration.json
```

Submit after reviewing calibration:

```powershell
python -m rft.scripts.submit voucher `
  --calibration .foundry\voucher-calibration.json `
  --data-dir rft\data `
  --grader-version v3 `
  --confirm-submit `
  --output .foundry\voucher-submission.json
```

The selected Voucher checkpoint is Step 10:

- canonical mean score: `0.924757`;
- mean latency: `23.098198` seconds;
- mean total tokens: `29609.611`.

## Hardened Web (static) v5

The v5 reward requires:

- exact recommended and terminal product IDs;
- an accepted resolved clue;
- selected-product inspection before recommendation;
- a grounded final answer containing the clue and product identity;
- exactly one recommendation followed by termination;
- bounded, non-repeated catalog search.

Wrong products are capped at `0.20`. Missing clue, grounded answer, or protocol
completion is capped at `0.48`, below the `0.90` pass threshold.

Calibrate:

```powershell
python -m rft.scripts.calibrate `
  --results rft\jobs\web-v5\base-calibration-results.jsonl `
  --dataset rft\data\web-validation-eval.jsonl `
  --grader-version web-v5 `
  --output .foundry\web-v5-calibration.json
```

Submit:

```powershell
python -m rft.scripts.submit web `
  --calibration .foundry\web-v5-calibration.json `
  --data-dir rft\data `
  --grader-version web-v5 `
  --eval-interval 2 `
  --confirm-submit `
  --output .foundry\web-v5-submission.json
```

The selected final checkpoint produced:

- canonical mean score: `0.717500`;
- v5 reward mean: `0.666111`;
- mean latency: `48.751820` seconds;
- recorded mean total tokens: `37277.933`.

Development receipts are under `evals/results/checkpoints/web-v5/`. Calibration,
submission, status, deployment, and selection receipts are under
`rft/jobs/web-v5/`. The optimized agent keeps the best Web (static) score; this
checkpoint is published as the lower-recorded-token alternative. Recorded tokens
and costs exclude unavailable usage from failed episodes.

## Web (live) v6

Web (live) trains only the catalog agent. Each training row contains the
optimized instructions, the user query, and web evidence captured once by the
fixed lookup step, so the trainer sees exactly what the deployed agent sees.
Data, captures, and the curation review are in `rft/data/web-search/`.

The reward combines:

- deterministic gates for the exact recommended and terminal product, product
  inspection before recommendation, and completed protocol;
- an LLM judge (GPT-5.4) that rates clue correctness against the reference
  answer, faithfulness to the captured evidence, product-claim support, and
  answer completeness; all four must be full to pass quality;
- a succinctness cost of 0.08 per tool call beyond
  search → inspect → recommend → terminate, 0.16 per repeated search or
  inspection, and 0.005 per final-answer word beyond 30, capped at 0.40 so a
  correct answer always outscores any quality failure.

The pass threshold is 0.85. On frozen base-model rollouts the reward failed
28.9% of training samples, inside the 25-50% calibration target, and 19.0% of
validation samples.

The job (`rft/jobs/web-search-v6/`) trained for 3 epochs with batch size 8,
learning-rate multiplier 0.5, compute multiplier 1.5, medium reasoning, and at
most 8 episode steps. Validation reward rose from 0.845 to 0.907. The final
checkpoint scored 0.9025 on development (versus 0.8292 for the optimized agent)
and 0.9055 on the sealed final test. See `docs/reproducibility.md` for the
exact commands.

## Published reward endpoints

| Endpoint | Purpose |
|---|---|
| `POST /grade/v3` | Voucher and general catalog RFT reward |
| `POST /grade/web/v5` | Hardened Web (static) reward |
| `POST /grade/web-search/v6` | Web (live) semantic reward |
| `POST /rft/tools/{tool_name}` | Catalog tool adapter for RFT episodes |

RFT is billable and availability depends on model, region, and subscription.
Recorded job and file IDs are evidence from the original runs and must not be
reused for new submissions.

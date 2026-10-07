# Reinforcement fine-tuning

RFT changes model behavior through live multi-step catalog-tool episodes. It
starts from the retained optimized agent configuration and uses deterministic
endpoint graders hosted by the ShoppingBench API.

The published RFT workflow covers:

- **Voucher**, using the canonical v3 reward;
- **Catalog Web**, using the hardened v5 clue and grounding reward.

Product and Shop were not trained because calibration did not show enough
useful base-model failures. Live Web Search RFT remains work in progress and
is intentionally excluded from the published data, configuration, and job
receipts.

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

## Hardened Catalog Web v5

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
- mean total tokens: `37277.933`.

Development receipts are under `evals/results/checkpoints/web-v5/`. Calibration,
submission, status, deployment, and selection receipts are under
`rft/jobs/web-v5/`.

## Published reward endpoints

| Endpoint | Purpose |
|---|---|
| `POST /grade/v3` | Voucher and general catalog RFT reward |
| `POST /grade/web/v5` | Hardened Catalog Web reward |
| `POST /rft/tools/{tool_name}` | Catalog tool adapter for RFT episodes |

RFT is billable and availability depends on model, region, and subscription.
Recorded job and file IDs are evidence from the original runs and must not be
reused for new submissions.

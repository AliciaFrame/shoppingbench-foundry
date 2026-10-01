# Agentic reinforcement fine-tuning

RFT changes the model policy using live multi-step tool episodes and a
calibrated reward. The starting developer message is composed from the retained
Agent Optimizer configuration, so the training policy begins from the best
known agent behavior.

## Workflow

1. Generate disjoint train/validation rows while excluding canonical holdouts.
   Every row includes the same four function schemas used during calibration.
2. Run base-model rollouts through the same live tools.
3. Calibrate a pass threshold targeting a 25–50% failure rate.
4. Submit one independently suffixed job per task.
5. Monitor every checkpoint.
6. Evaluate checkpoints on the untouched canonical holdout.
7. Combine the selected checkpoint with the retained optimized agent config.

## Prepare

```powershell
python -m rft.scripts.prepare_data
```

Generated sizes:

| Task | Train | Validation |
|---|---:|---:|
| Product | 180 | 20 |
| Shop | 180 | 20 |
| Voucher | 180 | 20 |
| Web | 80 | 20 |

## Calibration

```powershell
python -m rft.scripts.calibrate `
  --results rft\results\web-validation-results.jsonl `
  --dataset rft\data\web-validation-eval.jsonl `
  --output rft\results\web-calibration.json
```

Web produced a 40% base failure rate at calibrated threshold `0.9`, providing
useful training signal. Product produced only 15% failures over 60 repeated
rollouts and is intentionally gated pending a harder curriculum.

## Submit

RFT access and supported endpoints are subscription/preview dependent. Set the
variables in `.env.example`, then:

```powershell
python -m rft.scripts.submit web `
  --calibration rft\results\web-calibration.json `
  --confirm-submit `
  --output rft\results\web-job-rft2.json
```

The corrected Web job uses:

- suffix `mai-sb-web-rft2`
- model checkpoint `mai-code-1.1-flash-2026-08-27`
- public `/openai/v1` reinforcement fine-tuning API
- calibrated pass threshold `0.9`
- authenticated live tools and canonical endpoint grader

The first receipt, `web-job.json`, records a failed wiring attempt. Its uploaded
rows omitted the per-example function schemas required by agentic RFT. The
platform reached the grader 260 times but made zero tool calls, then rejected
all 160 validation attempts as hard before step 1 and billed 0.000 training
hours. Submission now validates that every row contains the exact tool names
and schemas used by the job configuration.

Monitor:

```powershell
python -m rft.scripts.monitor <job-id> `
  --output rft\results\web-status.json
```

Result receipts are kept under `results/`; the README will be updated with
checkpoint comparisons when training and evaluation complete. Each
`web-job*.json` records the immutable state returned at submission. Live
`*-status.json` and `*-events.jsonl` files are generated locally and ignored.

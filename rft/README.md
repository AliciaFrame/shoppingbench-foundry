# Agentic reinforcement fine-tuning

RFT changes the model policy using live multi-step tool episodes and a
calibrated reward. The starting developer message is composed from the retained
Agent Optimizer configuration, so the training policy begins from the best
known agent behavior.

## Workflow

1. Generate disjoint train/validation rows while excluding canonical holdouts.
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

Web produced a 40% base failure rate at canonical threshold `1.0`, providing
useful training signal. Product produced only 15% failures over 60 repeated
rollouts and is intentionally gated pending a harder curriculum.

## Submit

RFT access and supported endpoints are subscription/preview dependent. Set the
variables in `.env.example`, then:

```powershell
python -m rft.scripts.submit web `
  --calibration rft\results\web-calibration.json `
  --private-preview `
  --pass-threshold 1.0 `
  --confirm-submit `
  --output rft\results\web-job.json
```

The accepted Web job uses:

- suffix `mai-sb-web-rft1`
- model alias `mai-code-1.1-flash`
- resolved checkpoint `MAI-Code-1.1-Flash-2026-09-15`
- Blossom recipe `mai-code-1-flash`, version 11
- authenticated live tools and canonical endpoint grader

Monitor:

```powershell
python -m rft.scripts.monitor <job-id> `
  --output rft\results\web-status.json
```

Result receipts are kept under `results/`; the README will be updated with
checkpoint comparisons when training and evaluation complete. The current
receipt records that training has started and the validation evaluation has
been created.

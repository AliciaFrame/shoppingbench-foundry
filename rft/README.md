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

### Web grader v2

The active `rft2` job continues to use the immutable v1 scoring contract:

- 55% exact product ID
- 15% literal knowledge attribute anywhere in any search query
- 10% viewed before recommendation
- 5% exactly one recommendation
- 5% recommendation immediately followed by termination
- 10% one-to-limit distinct searches

That contract can reward repeated searching because its literal-knowledge bonus
is larger than its all-or-nothing efficiency bonus. The v2 grader in
`src/shoppingbench_foundry/rft_grading_v2.py` is isolated from the v1 endpoint:

- 65% exact product ID
- 10% viewed before recommendation
- 5% exactly one recommendation
- 5% recommendation immediately followed by termination
- 10% literal knowledge attribute within the first two searches
- 5% one-to-limit distinct searches
- 3% penalty per excess search and 5% penalty per duplicate search

At threshold `0.9`, a correct, viewed, efficiently searched product with a
complete recommendation/termination sequence passes even if the inferred fact
is phrased differently. Late keyword insertion cannot recover the knowledge
bonus, and search bloat reduces the score.

Recalibrate from a downloaded Foundry Step 0 output-items response:

```powershell
python -m rft.scripts.recalibrate_step0 `
  --step0 path\to\rft-step0-eval.json `
  --output rft\results\web-step0-calibration-v2.json
```

The v2 endpoint is `/grade/v2`; `/grade` remains v1 so the `rft2` job was not
changed mid-run. The separately submitted `developerTier` job uses:

- job `ftjob-8978aa9f7a7d40eea2fa3854babb72da`
- suffix `mai-sb-web-rft3`
- model `mai-code-1.1-flash-2026-08-27`
- pass threshold `0.9`
- receipt `rft/results/web-job-rft3.json`

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

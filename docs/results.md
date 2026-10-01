# Results

## Canonical Agent Optimizer results

| Task | Baseline | Retained | Delta |
|---|---:|---:|---:|
| Product | 0.753 | 0.961 | +0.208 |
| Shop | 0.860 | 0.998 | +0.138 |
| Voucher | 0.898 | 0.955 | +0.057 |
| Web | 0.790 | 0.790 | 0.000 |

The final cross-task mean is 0.926.

## Important interpretation

Optimizer scores and canonical scores answer different questions:

- The optimizer's `task_adherence` score guides search on optimization cases.
- The canonical score deterministically measures task correctness on untouched
  holdouts.

A candidate can improve the optimizer score and still be rejected. Voucher
round two improved termination but reduced recommendation correctness; Web
round two tied the mean while reducing perfect and terminated cases.

## RFT

Web is the first RFT task because its calibrated 40% base failure rate offers
enough room to learn. Product remains a curriculum-design task: its 15%
repeated-rollout failure rate is too close to ceiling for a strong first RFT
experiment.

The first Web submission reached the endpoint grader but made zero tool calls
because the uploaded rows omitted the per-example function schemas required by
agentic RFT. The platform rejected all 160 validation attempts before step 1
and billed 0.000 training hours. The generator and submit-time validation now
enforce the four schemas, and the deployed endpoint uses the same reward
function used during calibration.

Corrected job `ftjob-e7e676861873489195e796bf622ee265` was accepted on the
August 27 MAI-Code-1.1-Flash checkpoint with suffix `mai-sb-web-rft2`. Its
committed job JSON preserves the submission receipt; live status is monitored
separately. Checkpoint and final-model results will be added after every
checkpoint is evaluated on the canonical holdout.

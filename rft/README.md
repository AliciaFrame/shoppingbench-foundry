# Agentic reinforcement fine-tuning

RFT changes the model policy using live multi-step tool episodes and a
calibrated reward. The starting developer message is composed from the retained
Agent Optimizer configuration, so the training policy begins from the best
known agent behavior.

## Workflow

1. Generate connected-group-disjoint training, validation, checkpoint
   development, and sealed final-test rows. Every row includes the same four
   function schemas used during calibration.
2. Run five base-model rollouts per validation case through the same live tools.
3. Calibrate a pass threshold targeting a 25–50% failure rate, with at least 60
   clustered observations and a bootstrap interval.
4. Submit one independently suffixed job per task.
5. Monitor every checkpoint.
6. Select checkpoints on the development set. Open the sealed final test once,
   only after all model and agent choices are frozen.
7. Combine the selected checkpoint with the retained optimized agent config.

## Prepare

```powershell
python -m rft.scripts.prepare_data --output-dir rft\data\v2
```

Generated sizes:

| Task | Train | Validation |
|---|---:|---:|
| Product | 150 | 20 |
| Shop | 150 | 20 |
| Voucher | 150 | 20 |
| Web | 50 | 20 |

## Calibration

```powershell
python -m rft.scripts.calibrate `
  --results rft\results\v2\web-calibration-results.jsonl `
  --dataset rft\data\v2\web-calibration-eval.jsonl `
  --grader-version v3 `
  --output rft\results\v2\web-calibration.json
```

### Hardened grader v3

Historical `/grade` and `/grade/v2` endpoints remain unchanged for v1
reproducibility. New jobs use `/grade/v3`:

- 70% exact ordered selection
- 8% proof that every selected ID was viewed before recommendation
- 4% search tied to user constraints
- 2% exactly one recommendation
- 4% recommendation immediately followed by termination
- 10% valid user-facing final answer
- 2% efficient distinct searches, available only for exact selections

Web final answers must state the resolved clue. Voucher final answers must
contain the recomputed subtotal, threshold, discount, final payable, budget
check, and shop consistency when applicable. Wrong products, dummy views, and
empty final answers cannot reach the pass threshold.

Web produced a 40% base failure rate at calibrated threshold `0.9`, providing
useful training signal. Product produced only 15% failures over 60 repeated
rollouts and is intentionally gated pending a harder curriculum.

### Web grader v2

The canceled `rft2` job used the immutable v1 scoring contract:

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

The v2 endpoint is `/grade/v2`; `/grade` remains v1 so historical jobs stay
reproducible. The successful `developerTier` job used:

- job `ftjob-8978aa9f7a7d40eea2fa3854babb72da`
- suffix `mai-sb-web-rft3`
- model `mai-code-1.1-flash-2026-08-27`
- pass threshold `0.9`
- receipt `rft/results/web-job-rft3.json`

The exact billable submission is preserved as
`scripts/submit_web_rft3.ps1`. It requires an explicit `-ConfirmSubmit` switch
and defaults to a new receipt path so the original immutable receipt is not
overwritten.

## Historical v1 Web RFT result

The job completed with checkpoints at Steps 10, 15, and 19/final. Every
artifact was deployed separately and evaluated on the unchanged 50-case Web
holdout using the retained agent configuration.

| Artifact | Mean | Perfect | Exact | Terminated | Mean searches | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Base model | 0.790 | 32 | 38 | 40 | 16.82 | 142,051 |
| Step 10 | **0.901** | **43** | **44** | **47** | 6.24 | 49,321 |
| Step 15 | 0.836 | 40 | 40 | 46 | **4.84** | **45,896** |
| Final | 0.795 | 36 | 39 | 40 | 5.70 | 68,899 |

Step 10 is the selected checkpoint. It improved 13 cases, tied 34, and
regressed three relative to the base model. Later checkpoints became more
search-efficient but lost exact-product accuracy, so deploying the final model
would have discarded nearly the entire quality gain.

The post-RFT Agent Optimizer experiment completed as
`opt_01bdafd831564fa69b18610f4594b8e2`. Candidate 1 won the optimizer judge
(`0.5885` versus `0.584625`) and reduced mean tokens from 49,321 to 44,904 on
the canonical holdout, but it reduced mean score to `0.871` and exact product
selection to 41/50. It was rejected without deployment. This is the final
plateau signal for the current Web curriculum.

Regenerate the tracked comparison:

```powershell
python -m rft.scripts.summarize_checkpoints `
  --baseline optimization\results\raw\web-round2-baseline-heldout.jsonl `
  --candidate step10 .foundry\results\web-rft3-step10-holdout.jsonl `
  --candidate step15 .foundry\results\web-rft3-step15-holdout.jsonl `
  --candidate final .foundry\results\web-rft3-final-holdout.jsonl `
  --output rft\results\web-rft3-holdout-comparison.json
```

The complete aggregate and case-level win/loss lists are in
[`results/web-rft3-holdout-comparison.json`](results/web-rft3-holdout-comparison.json).
The three underlying 50-case receipts are preserved under
[`results/raw`](results/raw) and are checked against the summary in CI.

## Historical v1 training interpretation

The Web curve indicates saturation of the current curriculum rather than
saturation of the model. Step 10 is the end-of-first-epoch winner; continuing
training reduced holdout accuracy. Do not submit another Web run until new,
disjoint hard cases cover date/issue mapping, numeric identifiers, ambiguous
names, compatibility-list attributes, and hard negative products.

Product and Shop remain gated because calibration produced only 15–20%
failures. Voucher is the only other task above the signal floor:

- calibrated failure rate: 35%
- pass threshold: `0.975`
- job: `ftjob-3f87f6185a6a436b9255901810c63fee`
- suffix: `mai-sb-vouch-rft1`
- training type: `developerTier`
- one epoch
- batch size 8
- learning-rate multiplier 0.5
- evaluation every three steps with three samples
- receipt: `rft/results/voucher-job-rft1.json`

The exact follow-up command is preserved as
`scripts/submit_voucher_rft1.ps1`.

### Current v2 Voucher RFT result

The v2 Voucher curriculum used grader v3, a `0.98` pass threshold, 12 maximum
episode steps, and connected-group data. Calibration covered 20 cases with five
rollouts each and measured a `29%` base failure rate (`95%` bootstrap interval
`[16%, 44%]`), which met the predeclared signal gate.

- job: `ftjob-df0cb6a4bf3649ea862b0988f6f6a9dc`
- suffix: `mai-sb-vouch-rft2`
- training type: `developerTier`
- epochs: 2
- batch size: 8
- learning-rate multiplier: 1.0
- evaluation interval/samples: 5/10
- trained tokens: 573,336
- submission receipt: [`results/v2/voucher-v3-submission.json`](results/v2/voucher-v3-submission.json)

All published artifacts were evaluated on the same 30-case development split
with three rollouts per case under the final sequential runtime.

| Artifact | Mean | Exact | Success | Valid final answer | Mean tokens |
|---|---:|---:|---:|---:|---:|
| Historical Step 12 retained model | 0.9029 | 81/90 | 74/90 | 75/90 | 38,076 |
| **v3 Step 10 (selected)** | **0.9248** | 83/90 | **78/90** | **78/90** | 29,610 |
| v3 Step 15 | 0.8980 | 80/90 | 74/90 | 77/90 | **29,077** |
| v3 final | **0.9253** | **84/90** | 71/90 | 71/90 | 30,950 |

Step 10 was selected instead of the numerically highest-mean final checkpoint.
The final checkpoint's one extra exact rollout was within uncertainty, while it
lost seven complete successes and valid final answers, used more tokens, and
was slower. The direct paired comparison is
[`../evaluations/results/v2/voucher-v3-final-vs-step10.json`](../evaluations/results/v2/voucher-v3-final-vs-step10.json).

### Current v2 Web RFT experiment

The hardened Web calibration completed on 20 connected-group validation cases
with five rollouts each:

- samples: 100
- mean score: `0.6832`
- selected pass threshold: `0.90`
- base failure rate: `37%`
- clustered bootstrap interval: `[19%, 56%]`
- sufficient signal: yes

This meets the predeclared 25-50% failure gate. Job
`ftjob-65084bb6362f47188077b95805058cc0` was submitted with suffix
`mai-sb-web-rft4`, grader v3, two epochs, batch size 8, learning-rate
multiplier `0.5`, evaluation every three steps with five validation samples,
and a 12-step episode bound. The conservative learning rate responds to the
historical Web run, where later checkpoints overfit after Step 10.

Receipts:

- [`results/v2/web-v3-calibration.json`](results/v2/web-v3-calibration.json)
- [`results/v2/web-v3-submission.json`](results/v2/web-v3-submission.json)
- [`scripts/submit_web_rft4.ps1`](scripts/submit_web_rft4.ps1)

The job succeeded with deployable checkpoints at Steps 6, 9, and 12/final.
All three were evaluated on the same 30-case, three-rollout development gate:

| Artifact | Mean | Exact | Success | Mean tokens | Mean latency |
|---|---:|---:|---:|---:|---:|
| **Historical Step 10 (selected)** | **0.7381** | **64/90** | **61/90** | **59,853** | **41.91s** |
| RFT4 Step 6 | 0.6500 | 59/90 | 45/90 | 68,757 | 49.25s |
| RFT4 Step 9 | 0.6717 | 60/90 | 54/90 | 79,135 | 54.11s |
| RFT4 final/Step 12 | 0.6669 | 59/90 | 57/90 | 88,009 | 59.97s |

Every RFT4 checkpoint regressed on mean score, exact selection, complete
success, cost, latency, and paired case outcomes. The historical Step 10
deployment remains selected, and no new hosted Web version was promoted.

Comparison receipts:

- [`../evaluations/results/v2/web-v3-step6-vs-retained-step10.json`](../evaluations/results/v2/web-v3-step6-vs-retained-step10.json)
- [`../evaluations/results/v2/web-v3-step9-vs-retained-step10.json`](../evaluations/results/v2/web-v3-step9-vs-retained-step10.json)
- [`../evaluations/results/v2/web-v3-final-vs-retained-step10.json`](../evaluations/results/v2/web-v3-final-vs-retained-step10.json)

### Historical v1 Voucher RFT result

The job completed successfully with checkpoints at Steps 3, 12, and 15/final.
Step 3 and Step 12 were deployed to the approved ShoppingBench resource and
evaluated on the unchanged 50-case holdout.

| Artifact | Mean | Perfect | Exact | Terminated | Mean searches | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Retained optimized agent | 0.9545 | 26 | 46 | 29 | 6.30 | 26,311 |
| Step 3 | 0.96375 | 46 | 47 | 49 | 7.64 | 45,922 |
| Step 12 | **0.994667** | **48** | **49** | **49** | **5.78** | **34,788** |

Step 12 improved 24 cases, tied 26, and regressed none under the v1 score.
Exact selection improved by three cases, while termination improved by 20 net
cases; the evidence primarily supports protocol improvement, not improved
communicated arithmetic. Step 3 regressed three cases and used more tokens; the
lower-reward Step 15/final artifact was not deployed. The aggregate receipt is
[`results/voucher-rft1-holdout-comparison.json`](results/voucher-rft1-holdout-comparison.json).

## Submit

RFT access and supported endpoints are subscription/preview dependent. Set the
variables in `.env.example`, then:

```powershell
python -m rft.scripts.submit web `
  --calibration rft\results\web-calibration.json `
  --n-epochs 2 `
  --batch-size 8 `
  --learning-rate-multiplier 1.0 `
  --eval-interval 5 `
  --eval-samples 1 `
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
  --output rft\results\web-status.json `
  --watch `
  --interval 120
```

The monitor uses Microsoft Entra authentication when
`AZURE_OPENAI_API_KEY` is absent and refreshes the saved status receipt until
the job reaches a terminal state.

Result receipts are kept under `results/`. Each `*-job*.json` records the
immutable state returned at submission. Live `*-status.json` and
`*-events.jsonl` files are generated locally and ignored.

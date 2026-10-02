# Results

## Canonical Agent Optimizer results

| Task | Baseline | Retained | Delta |
|---|---:|---:|---:|
| Product | 0.753 | 0.961 | +0.208 |
| Shop | 0.860 | 0.998 | +0.138 |
| Voucher | 0.898 | 0.995 | +0.097 |
| Web | 0.790 | 0.901 | +0.111 |

The final selected cross-task mean is 0.964.

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
committed job JSON preserves the submission receipt. It was canceled after its
validation score stayed flat and training behavior showed reward-hacking
signals; no checkpoint was retained.

A second `developerTier` job, `ftjob-8978aa9f7a7d40eea2fa3854babb72da`,
completed with suffix `mai-sb-web-rft3` against the isolated v2 grader.
The revised reward makes exact product selection dominant, limits the
knowledge-query bonus to the first two searches, and penalizes excess and
duplicate searches. Recalibration on the authoritative 20-case Step 0 run
produced a 50% base failure rate at threshold `0.9`.

## Web checkpoint selection

All three published artifacts were deployed and evaluated on the same
50-case canonical Web holdout with the retained agent instructions, skill, and
live shopping tools.

| Artifact | Mean | Perfect | Exact | Terminated | Mean searches | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Base model | 0.790 | 32 | 38 | 40 | 16.82 | 142,051 |
| Step 10 | **0.901** | **43** | **44** | **47** | 6.24 | 49,321 |
| Step 15 | 0.836 | 40 | 40 | 46 | **4.84** | **45,896** |
| Final | 0.795 | 36 | 39 | 40 | 5.70 | 68,899 |

Step 10 improved 13 cases, tied 34, and regressed three relative to the base
model. Step 15 and the final checkpoint lost exact-product accuracy, showing
that the second epoch overfit the training reward. Step 10 is therefore the
selected artifact.

The result is not evidence that Web is globally saturated. It is evidence that
the current 80/20 curriculum is saturated: another run should wait for new,
disjoint hard cases and better grounding labels rather than recycling the same
examples.

## Post-RFT Web optimizer

The optimizer generated four candidates around the selected Step 10 policy.
Candidate 1 won its LLM-judge evaluation, but the canonical holdout rejected
it.

| Artifact | Optimizer score | Canonical mean | Perfect | Exact | Terminated | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Step 10 retained config | 0.584625 | **0.901** | **43** | **44** | 47 | 49,321 |
| Candidate 1 | **0.5885** | 0.871 | 40 | 41 | **49** | **44,904** |

Candidate 1 improved three cases, tied 43, and regressed four. The result
confirms a plateau: the current curriculum can trade exact accuracy for
efficiency, but does not provide a higher-quality operating point.

## Additional RFT gates

| Task | Calibrated failure | Decision |
|---|---:|---|
| Product | 15% across repeated rollouts | Do not train; insufficient signal |
| Shop | 20% | Do not train; canonical score already 0.998 |
| Voucher | 35% | Run one conservative experiment |
| Web | 50% under grader v2 | Completed; Step 10 retained |

Voucher job `ftjob-3f87f6185a6a436b9255901810c63fee` uses one epoch,
learning-rate multiplier `0.5`, evaluation every three steps, and three
validation samples. These settings intentionally reduce the overtraining risk
observed in Web.

## Voucher checkpoint selection

The Voucher job completed with checkpoints at Steps 3, 12, and 15/final.
Step 3 and Step 12 were deployed to the approved ShoppingBench resource and
evaluated on the unchanged 50-case holdout.

| Artifact | Mean | Perfect | Exact | Terminated | Mean searches | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| Retained optimized agent | 0.9545 | 26 | 46 | 29 | 6.30 | 26,311 |
| Step 3 | 0.96375 | 46 | 47 | 49 | 7.64 | 45,922 |
| Step 12 | **0.994667** | **48** | **49** | **49** | **5.78** | **34,788** |

Step 12 improved 24 cases, tied 26, and regressed none. Step 3 improved the
mean but regressed three cases and used substantially more tokens. Step 12 is
therefore retained; the lower-reward final artifact was not deployed.

# Results

> **Methodology status:** the tables below preserve the historical v1
> experiment. The case-held-out sets were repeatedly used for model selection,
> so they are development evidence rather than sealed final-test estimates.
> Web also had three product-overlap cases. The v2 remediation, raw sensitivity
> analysis, and immutable v1 hashes are documented in
> [`audits/v1-methodology-audit.md`](audits/v1-methodology-audit.md).

## Sealed v2 final-test results

All configuration and checkpoint choices were frozen before these disjoint
50-case sets were opened. Each case received one attempt, and the results below
are the final generalization estimates rather than another selection gate.

| Task | Mean | Exact | Success | Valid final answer | Mean tokens | Mean latency |
|---|---:|---:|---:|---:|---:|---:|
| Product | **0.9385** | 46/50 | 46/50 | 49/50 | 11,975 | 16.79s |
| Shop | **0.9047** | 45/50 | 44/50 | 45/50 | 34,917 | 30.37s |
| Voucher | **0.8625** | 43/50 | 40/50 | 41/50 | 31,349 | 28.88s |
| Web | **0.6305** | 32/50 | 20/50 | 20/50 | 64,083 | 60.70s |

The Shop receipt contains one malformed-ID tool call that returned HTTP 500.
The Voucher receipt contains one malformed-ID tool failure and one Azure
content-filter rejection. These episodes were recorded as zero-score failures
without rerolls. Web had no operational errors; its 20/50 complete-success
rate is a genuine generalization gap and the clearest target for a future,
new-curriculum experiment. These sealed results did not trigger further model
selection or training.

Receipts:

- [`product-final-test.jsonl`](../evaluations/results/v2/product-final-test.jsonl)
- [`shop-final-test.jsonl`](../evaluations/results/v2/shop-final-test.jsonl)
- [`voucher-final-test.jsonl`](../evaluations/results/v2/voucher-final-test.jsonl)
- [`web-final-test.jsonl`](../evaluations/results/v2/web-final-test.jsonl)

## Voucher v2 development selection

Voucher v3 is the first completed checkpoint comparison under the corrected v2
contract: connected-group splits, three rollouts for each of 30 development
cases, sequential recommendation and termination, a separate grounded final
answer, and at most 12 processed tool steps. These results select a model; they
are not sealed final-test estimates.

| Artifact | Mean | Exact | Success | Valid final answer | Mean tokens | Mean latency |
|---|---:|---:|---:|---:|---:|---:|
| Historical Step 12 retained model | 0.9029 | 81/90 | 74/90 | 75/90 | 38,076 | 26.88s |
| **v3 Step 10 (selected)** | **0.9248** | 83/90 | **78/90** | **78/90** | 29,610 | **23.10s** |
| v3 Step 15 | 0.8980 | 80/90 | 74/90 | 77/90 | **29,077** | 24.86s |
| v3 final | **0.9253** | **84/90** | 71/90 | 71/90 | 30,950 | 25.24s |

Step 10 improved over the retained Step 12 model by `+0.0219` mean score,
`+2/90` exact selections, and `+4/90` complete successes while reducing mean
token use by 8,466. The clustered intervals include zero, so the result should
be described as the better observed operating point rather than a proven
population-level gain.

The v3 final checkpoint was not selected. Relative to Step 10, its `+1/90`
exact delta was inconclusive (`95%` clustered interval `[-0.0556, 0.0889]`),
while success fell by `7/90` (`95%` interval `[-0.1444, -0.0000]`), valid
final answers fell by `7/90`, mean tokens rose by 1,340, and latency rose by
2.14 seconds. Case-level outcomes favored Step 10: the final checkpoint won
four cases, Step 10 won ten, and sixteen tied.

Receipts:

- [`voucher-v3-step10-vs-step12.json`](../evaluations/results/v2/voucher-v3-step10-vs-step12.json)
- [`voucher-v3-final-vs-step10.json`](../evaluations/results/v2/voucher-v3-final-vs-step10.json)
- [`voucher-v3-step15-vs-step12.json`](../evaluations/results/v2/voucher-v3-step15-vs-step12.json)
- [`voucher-v3-final-vs-step12.json`](../evaluations/results/v2/voucher-v3-final-vs-step12.json)

## Product v2 development result

The retained Product agent completed the same 30-case, three-rollout
development evaluation under the final 12-step sequential runtime:

| Mean | Exact | Success | Valid final answer | Mean tokens | Mean latency |
|---:|---:|---:|---:|---:|---:|
| 0.9408 | 84/90 | 74/90 | 87/90 | 13,948 | 16.22s |

This is leakage-safe development evidence for the retained agent configuration,
not a sealed final-test estimate. The receipt is
[`product-development-3x.jsonl`](../evaluations/results/v2/product-development-3x.jsonl).

## Shop v2 development result

The retained Shop agent also completed 30 development cases with three
rollouts each under the final runtime:

| Mean | Exact | Success | Valid final answer | Mean tokens | Mean latency |
|---:|---:|---:|---:|---:|---:|
| 0.9371 | 84/90 | 84/90 | 85/90 | 33,799 | 25.56s |

The receipt is
[`shop-development-3x.jsonl`](../evaluations/results/v2/shop-development-3x.jsonl).
As with the other v2 development results, this is checkpoint/configuration
selection evidence rather than the sealed final estimate.

## Web v2 development selection

The hardened grader-v3 Web RFT job completed successfully, and all three
deployable checkpoints were evaluated on the same 30-case development split
with three rollouts per case.

| Artifact | Mean | Exact | Success | Valid final answer | Mean tokens | Mean latency |
|---|---:|---:|---:|---:|---:|---:|
| **Historical Step 10 (selected)** | **0.7381** | **64/90** | **61/90** | **64/90** | **59,853** | **41.91s** |
| RFT4 Step 6 | 0.6500 | 59/90 | 45/90 | 46/90 | 68,757 | 49.25s |
| RFT4 Step 9 | 0.6717 | 60/90 | 54/90 | 55/90 | 79,135 | 54.11s |
| RFT4 final/Step 12 | 0.6669 | 59/90 | 57/90 | 57/90 | 88,009 | 59.97s |

The paired clustered comparisons reject all three new checkpoints. Mean-score
deltas versus retained Step 10 were `-0.0881`, `-0.0664`, and `-0.0711`;
Step 10 won the paired case comparison 16-3 against Step 6, 13-2 against Step
9, and 10-4 against the final checkpoint. Every new checkpoint also used more
tokens and added latency. Historical Step 10 remains the frozen Web operating
point, and the hosted Web agent was not redeployed.

Receipts:

- [`web-v3-step6-vs-retained-step10.json`](../evaluations/results/v2/web-v3-step6-vs-retained-step10.json)
- [`web-v3-step9-vs-retained-step10.json`](../evaluations/results/v2/web-v3-step9-vs-retained-step10.json)
- [`web-v3-final-vs-retained-step10.json`](../evaluations/results/v2/web-v3-final-vs-retained-step10.json)

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
- The v1 canonical score deterministically measures task correctness on
  case-held-out development sets.

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

Step 12 improved 24 cases, tied 26, and regressed none under the v1 aggregate
score. Exact selection improved by three cases, while termination improved by
20 net cases. The strongest supported interpretation is improved protocol
completion with a smaller bundle-selection gain; the original traces do not
demonstrate improved user-facing voucher arithmetic.

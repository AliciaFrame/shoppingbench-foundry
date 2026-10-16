# RFT only when justified

## Decide whether to train

RFT targets the model policy, not merely prompts. Start from the retained
agent-optimized configuration, not an unrelated vanilla prompt. Identify
remaining learnable behavior, the policy model being trained, and any fixed
external components (retrieval, web lookup, business APIs, other models).

Verify model/version, region, supported training SKU, authorization, quota
and agentic-tool support using current Foundry capabilities. The ability to
call inference or ordinary fine-tuning does not prove agentic RFT support.
If unsupported, explain the block and retain the optimized agent; do not
quietly substitute a different training method.

## Prepare domain-specific training data

- Keep train/validation, optimizer search, development and final groups
  separate. Review duplicate entities, documents, sessions and connected
  tool state, not just identical query strings.
- Bind each example to the optimized instructions/tool contract. Validate
  required messages, per-example schemas, references and final output shape
  against the selected training service's current requirements.
- Keep reference labels out of agent-visible inputs. If an external step
  cannot execute during training, capture its evidence once, review it,
  freeze it as trusted input, and later test the full live serving pipeline.
- Review ambiguous premises and label/evidence contradictions. Explicitly
  quarantine the whole connected group where needed; preserve the review
  and dataset provenance.
- Map tool names to real adapters with the API's exact arguments/item/result
  contract, authentication, bounded execution, and reset behavior. Do not
  assume ordinary chat tools, hosted tools, or native web tools can be used
  directly by every RFT service.

## Calibrate reward before submission

Use representative repeated rollouts from the optimized base policy.
Choose sampling and thresholds from the use case; no benchmark case count
or universal pass threshold is built into this skill.

Test correct/incorrect, grounded/ungrounded, incomplete protocol, malformed
outputs, and potential reward shortcuts. Establish:

1. Meaningful agreement with the frozen quality evaluator and important
   safety/business invariants.
2. Within-example reward variation that can support learning, with sufficient
   observations and a useful distribution rather than a saturated reward.
3. Identical intended scores for local and hosted grader paths.
4. The service's actual pass-threshold comparator; a cutoff of 1 can be
   impossible under a strict `score > threshold` rule.
5. Grader latency, error and throttle behavior under expected training
   concurrency. Infrastructure errors must not become favorable judgments.

The grader may combine deterministic constraints with a calibrated semantic
judge. Pin its rubric, judge deployment/options and trusted evidence. Do not
change reward mid-job. Higher training reward is not proof of higher task
quality.

For efficiency rewards, inspect what the trainer actually exposes. If it
does not send token usage, tool count/answer length are proxies, not measured
billed tokens or hidden reasoning. Measure actual token costs separately.

## Submit and monitor

Present the proposed dataset hashes, training target, calibrated reward,
tool adapters, epochs/batch/learning-rate settings, reasoning/compute budget,
episode bounds, SKU, judge capacity and spending limits. Obtain explicit
submission approval. Review current SDK/API schemas before constructing a
job; parameter support and billing differ by model and training type.

The OpenAI-compatible SDK path, where supported, uses files uploaded for
fine-tuning and a reinforcement method on `fine_tuning.jobs.create`.
Agentic jobs additionally bind tool adapters and a grader according to the
current service schema. A project's `/models` listing can be unavailable;
do not infer unsupported capabilities from that alone, or infer support
without separate verification.

Persist file/job IDs and the exact submitted configuration immediately.
Monitor the same job, reward/validation curves, grader errors, generated
tokens and cost. Set bounded polling and an approved cancellation policy.
Do not resubmit because a job is queued for capacity. Throttling requires a
capacity/concurrency repair, not silent grader failure or an unapproved
second training bill.

Keep train and validation results separate. If one split is used to justify
signal while another is used for submission checks, disclose that policy and
verify the evidence rather than hiding a weak validation distribution.

If the acceptance goal is to confirm startup and cancel, define startup before
submission. Accepted, pending and a message saying training started are not
enough: require a real metrics event with a nonnegative training step and
numeric training reward (or an equally direct provider-specific training-work
signal). Persist that event before cancelling the same durable job ID. Then
confirm terminal cancellation; do not submit another job to test cancellation.

## Evaluate checkpoints

Record all deployable checkpoints and their hosting charges. Deploy candidates
only within the approved budget, then evaluate the same development cases,
agent configuration and quality/cost policy. A later or final checkpoint may
regress. Use [selection and verification](select-verify.md), not training
reward alone, to retain a model.

Reference the current [Microsoft Foundry fine-tuning guidance](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/reinforcement-fine-tuning)
and the selected model's supported agentic RFT documentation. This reference
is a process contract, not a claim that every MAI/model subscription supports
the same reinforcement payload.

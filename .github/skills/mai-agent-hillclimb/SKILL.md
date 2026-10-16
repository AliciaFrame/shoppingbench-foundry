---
name: mai-agent-hillclimb
description: >-
  Scaffolds and deploys MAI-powered hosted agents on Microsoft Foundry, measures a
  baseline, runs Agent Optimizer, calibrates endpoint/tool graders and guarded
  reinforcement fine-tuning, and verifies quality, latency, and cost.
  Use for hosted-agent onboarding, agent optimization, RFT experiment planning,
  checkpoint comparisons, and token-cost calculations for any use case.
license: Apache-2.0
---

# MAI-agent-hillclimb

Run a measured journey: **set up -> baseline -> Agent Optimizer -> justified
RFT -> select and verify**. This skill is self-contained: it does not require
another Foundry skill, this repository's Python package, or a shopping domain.
MAI is the intended policy model family, not a hardcoded model deployment or
version. Respect an explicitly selected compatible model.

All links below are relative to this skill directory, called `SKILL_ROOT`.
Commands execute in the user's selected agent project, not necessarily this
repository. Never assume that an example's tools, data, models, or resource
identifiers exist in that project.

## Entry and context

1. Identify the requested phase and the selected agent root/environment.
   Inspect only that root's code, deployment manifest, and `.foundry` metadata.
   Resolve supplied values first; ask only for unresolved choices. If several
   agent roots or environments are plausible, require an explicit selection.
2. Read [the project contract](references/contract.md). Record the use case,
   agent/runtime, tools, evaluator, datasets, target metrics, budget, and
   acceptance policy before changing weights or searching configurations.
3. Discover available interfaces. If Azure MCP is installed, retrieve current
   Foundry tools and schemas before calling them. Otherwise use the documented
   Azure CLI, azd, SDK, or REST paths in the phase references. Another skill is
   optional, never a prerequisite.
   Discover pricing, quota, RBAC and Bicep routers as applicable too; do not
   assume every Azure MCP installation exposes a Foundry router or identical
   command names. Use the CLI/SDK fallback when a needed router is absent.
4. Reuse sound project adapters. For missing pieces, use
   [the executable runbook](references/runbook.md): it bundles a hosted starter,
   domain/tool hooks, canonical grader, optimizer evaluator, authenticated
   endpoint service, provisioning templates, and guarded CLI paths.
   Customize the rubric and datasets; never substitute example-domain quality.
5. Resume from evidence under `.foundry/hillclimb/<run-id>`, not remembered job
   names. Check recorded code, data, model, evaluator, and pricing fingerprints.
   A changed contract starts a new run; never silently mix receipts.

## Phase routing

For an existing optimization-ready hosted agent, start with the
[ordinary SDK happy path](references/runbook.md#existing-agent-sdk-happy-path):
**plain smoke -> built-in optimization -> monitor -> isolated candidate**.
It needs no custom evaluator registration, signing key or RFT service.
Use rich capture only when needed for independent evaluation; custom rubrics,
graders and RFT remain advanced opt-in paths.

Use [the executable runbook](references/runbook.md) for the actual commands and
bundled interfaces; phase references below provide the decision and evidence
requirements. No repository code or additional skill is needed.

For a new project, start with the bundled scaffold, then adapt its domain hooks:

```powershell
python <SKILL_ROOT>\scripts\journey.py scaffold --output <new-agent-project>
```

| Phase | Required reference | Exit evidence |
|---|---|---|
| Create/connect project, models, hosted agent, tools | [Setup and deployment](references/setup.md) | Active version, identities/permissions, successful real smoke invocation |
| Freeze and evaluate baseline | [Baseline and optimization](references/baseline-optimize.md) | Complete development receipts, quality/latency/usage/cost report |
| Search agent configurations | [Baseline and optimization](references/baseline-optimize.md) | Nominated candidates independently evaluated and retained under the acceptance policy |
| Calibrate and train if warranted | [RFT](references/rft.md) | Validated data, calibrated reward, parity check, approved job and monitoring evidence |
| Compare checkpoints and verify final selection | [Selection and verification](references/select-verify.md) | Selected configuration/weights, matched comparison, sealed final result, deployment smoke test |
| Retrieve prices and compute costs, at every phase | [Pricing and cost](references/pricing.md) | Dated source-backed price snapshot, measured-usage report, completeness and estimate labels |
| Diagnose blocked or failed steps | [Troubleshooting](references/troubleshooting.md) | Explicit cause and a validated repair; no success-shaped fallback |

Only read the references needed for the current phase. A user can stop after
baseline or configuration optimization. RFT is optional, not the definition
of success.

## Gates that apply throughout

- **Approval:** resource creation, permission grants, deployments, model
  inference, optimizer jobs, training, and checkpoint hosting can be billable
  or consequential. Confirm the scope, spending cap, and authorization before
  executing them. Do not pre-approve unrestricted shell tools in this skill.
- **Quality:** choose the evaluator before optimization. It may be a
  deterministic test suite, calibrated semantic judge, human rubric, or a
  combination. The same quality definition must compare all stages.
  For grounded generation, verify claim-level support from observed evidence:
  valid citations alone are not faithfulness. Calibrate semantic grading or
  source review with valid-link/unsupported-claim negative examples.
- **Evidence:** include every scheduled episode in results. Preserve failed
  episodes, observed per-call usage, elapsed time, and explicit errors.
  Missing usage is unknown, not evidence of free inference. Retry transport
  requests within a bounded policy, not successful samples until they win.
- **Isolation:** keep optimizer search, RFT training/validation, development
  selection, and sealed final data disjoint by the relevant connected group
  (customer, document, session, entity, tool state, or other domain unit).
- **Calibration:** training reward is not automatically evaluation quality.
  Prove that it distinguishes desirable behavior, preserves important
  invariants, and offers within-example variation before RFT.
- **Selection:** set quality, safety, latency, and cost trade-offs in advance.
  Neither an optimizer's judge score nor the final training checkpoint is an
  automatic winner.
- **Final test:** persist selection and `final_test_started` before inference.
  Never reopen the final split to tune or pick a different candidate.
- **Credentials:** use managed identity/Entra where supported. Store secret
  references, not secrets, in metadata or reports. Historical resource/job
  IDs are evidence, not configuration.
- **Evaluation boundary:** ordinary hosted deployments do not unwrap workload
  envelopes or expose rich traces. Isolated evaluation deployments require
  explicit server opt-in and signed capture requests backed by a secret-store
  key; request metadata alone never authorizes trace export.
- **State:** record pending/running/completed/failed operations and their IDs
  immediately. A monitoring timeout is not permission to submit another job.
  Clean up only resources or processes explicitly created for the run and
  approved for deletion; do not remove shared infrastructure.
  A disconnected local monitor is not a failed cloud job. Resume the recorded
  ID after repairing access; never submit a replacement to recover monitoring.

## Self-contained local cost tool

Read the pricing reference before normalizing provider usage. The helper uses
only Python's standard library and makes **no network, model, or deployment
calls**:

```powershell
python <SKILL_ROOT>\scripts\cost_report.py `
  --receipts <run-directory>\episodes.jsonl `
  --prices <run-directory>\prices.json `
  --output <run-directory>\cost-report.json
```

It supports multiple models/call purposes, cached reads and writes, explicit
context tiers, incomplete usage/rates, and separate run-level charges. It does
not fetch or invent prices, judge quality, or approve cloud actions.
For matched baseline/optimized/RFT savings, add `--baseline-stage` and supply
each stage's receipts. The pricing reference covers operation-ledger ingestion,
cost per successful task, savings, and guarded break-even/ROI projections.

An offline demonstration is included:

```powershell
python <SKILL_ROOT>\scripts\cost_report.py `
  --receipts <SKILL_ROOT>\examples\episodes.synthetic.jsonl `
  --prices <SKILL_ROOT>\examples\prices.synthetic.json
```

Those rates are deliberately synthetic, not a current MAI price quote.
Use [the optional ShoppingBench example](references/shoppingbench.md) only
when the user chooses that domain.

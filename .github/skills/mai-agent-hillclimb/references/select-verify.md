# Select and verify

Compare baseline, retained agent-optimized configurations and RFT checkpoints
using the same development cases, evaluator and reset/session policy. Match
groups and rollout counts; report any intentional differences in external
evidence or environment state.

## Selection policy

Apply the contract's constraints first: quality floor, safety/protocol
requirements, latency/cost ceilings, and approved regression tolerances.
Then select the desired operating point, not automatically the highest
training reward, optimizer score, latest checkpoint, or cheapest incomplete
receipt.

Use paired cases and suitable uncertainty analysis. Repeated rollouts from
one group are not independent observations; cluster/bootstrap by the domain
group when appropriate. The cost tool reports aggregates and coverage, not
a significance test or automatic winner.

Record:

- Candidate agent/configuration/weights and immutable version/image identity.
- Dataset, evaluator/judge, tool/external-state and price fingerprints.
- Quality and invariant metrics, failures, latency, measured tokens and
  recorded serving cost; missing usage/rates and estimate basis.
- Development deltas, uncertainty, acceptance decision, and why the final
  checkpoint was retained or rejected.

A quality winner and efficiency alternative can coexist. Label the trade-off
clearly. Comparing a tuned agent with another model's vanilla baseline is
valid only when labeled as such, not as a best-versus-best claim.

## One-time sealed verification

Persist the selected candidate and `final_test_started: true` before opening
the final split. Use a holdout that was not consulted for prompts, rewards,
thresholds, model selection or budget decisions.

Run the selected candidate once under the frozen contract. Retain failures,
usage and partial infrastructure evidence. Resume an interrupted operation
only under that identical contract and a documented procedure; do not
resample successful episodes or use final results to choose another model.

If the final result disappoints, report it. A new improvement campaign needs
a new final holdout or an explicit acknowledgment that the old split is no
longer sealed.

## Deploy and hand off

Deploy the selected version after approval, verify identity/permissions,
active status and a real smoke invocation. Document rollback, resource
ownership, recurring hosting charges and monitoring. Stop temporary local
helpers and remove only approved experiment resources.

Package a concise reproduction recipe with source/SDK versions, configuration,
data/evaluator provenance, price snapshot, raw/normalized receipts and
selection rationale. Do not publish secrets or private task data. Model
access, stochastic generation, external data, and temporary fine-tuned
deployment availability limit exact cloud reproduction; distinguish that
from deterministic local arithmetic.

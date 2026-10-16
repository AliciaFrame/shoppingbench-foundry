# Optional worked example: ShoppingBench

This is one domain adapter, not a dependency of MAI-agent-hillclimb.

The [ShoppingBench Foundry repository](https://github.com/AliciaFrame/shoppingbench-foundry)
demonstrates constrained retrieval, multi-item shop consistency, voucher
arithmetic, and knowledge-grounded shopping. Its hosted catalog tools and
graders are specific to that domain; do not use them to evaluate unrelated
agents.

Its workflow illustrates the reusable lessons:

- A true vanilla runtime is distinct from tuned orchestration.
- Configuration optimization can be enough; Product and Shop did not need RFT.
- Training starts from retained optimized instructions/tool contracts.
- A fixed web resolver can supply evidence to a trainable catalog policy.
- Semantic reward calibration and checkpoint evaluation are separate from
  canonical task evaluation.
- Failures and their observed token usage stay in the comparison.
- An efficiency alternative can trade a small quality difference for lower
  recorded token consumption.
- Development selection precedes a one-time sealed final test.

When this example is chosen, clone its current documented version and follow
its README and reproduction guide. Its `evals`, `optimize`, and `rft` scripts
are usable adapters only in that clone. Historical Azure IDs and published
prices are not configuration for a new environment.

For another use case, retain the journey and replace the agent, tools,
datasets, evaluator, reward and acceptance policy.

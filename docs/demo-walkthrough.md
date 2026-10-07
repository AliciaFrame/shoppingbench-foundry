# Demo walkthrough

## 1. Establish the task

Open the root README and explain that ShoppingBench measures behavior, not
plausible prose. The agent must search, inspect evidence, recommend exact
ordered IDs, terminate, and provide a grounded answer.

## 2. Show the clean lifecycle

Walk through:

- `agents/<task>/baseline` - minimal off-the-shelf configuration
- `agents/<task>/optimized` - retained optimized configuration
- `evals/datasets` - optimization, training, development, and final roles
- `evals/results` - evidence organized by lifecycle stage
- `optimize` - Agent Optimizer runner and seeds
- `rft` - training data, submission code, and job receipts

## 3. Explain the baseline

Open `agents/shared/baseline_runtime.py`. Point out that it does not force tool
choice, hide tools, enforce termination, or request a separate final answer.
This is what makes the first column a real off-the-shelf baseline.

Then open `agents/shared/runtime.py` to show the optimized orchestration:
sequential tools, a 12-step bound, recommendation followed by termination, and
a grounded final-answer turn.

## 4. Explain the grader

Open `src/shoppingbench_foundry/grading.py`. The canonical grader scores exact
selection, task constraints, process, and final answer. Agent Optimizer's LLM
judge proposes candidates; this deterministic grader decides promotion.

## 5. Present the hill climb

Use `docs/results.md`:

- Product: `0.7529 -> 0.9408`
- Shop: `0.7957 -> 0.9371`
- Voucher: `0.8547 -> 0.8606 -> 0.9248`
- Catalog Web: `0.6911 -> 0.7286 -> 0.7175`, with RFT cutting latency
  and tokens by roughly half versus base
- Live Web Search: `0.7636 -> 0.9047`, with RFT still WIP

Emphasize that most baseline failures were incomplete agent behavior, not an
inability to identify products.

## 6. Show checkpoint discipline

For Voucher, show why Step 10 is the selected operating point. For Catalog
Web, show the v5 hard gates and the score/cost tradeoff. For Live Web Search,
show that the Agent Optimizer result remains published while RFT is marked WIP.

## 7. Close with the sealed test

Show the final-test table in `docs/results.md`. Web's drop to `0.6305` is the
remaining generalization problem. No new tuning decision was made from the
sealed test.

Finish with: **use the cheapest effective improvement lever, but measure
quality, latency, and tokens on one deterministic evaluation spine.**

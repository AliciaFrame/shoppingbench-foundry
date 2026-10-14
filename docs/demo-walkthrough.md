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

- Product: `0.753 -> 0.941`
- Shop: `0.796 -> 0.937`
- Voucher: `0.855 -> 0.861 -> 0.925`
- Web (static): `0.691 -> 0.729`, with an RFT alternative at `0.718` for
  half the latency and about a third of the cost
- Web (live): `0.250 -> 0.829 -> 0.903`, both steps statistically clear

Emphasize that most baseline failures were incomplete agent behavior, not an
inability to identify products.

## 6. Show checkpoint discipline

For Voucher, show why Step 10 is the selected operating point. For Web
(static), show the v5 hard gates and the score/cost trade-off. For Web (live),
show the training curve in `rft/jobs/web-search-v6/` and the paired comparison
in `evals/results/comparisons/web-search-optimized-to-rft.json`.

## 7. Close with the sealed test

Show the final-test table in `docs/results.md`. Web (live) RFT scores `0.906`
on the sealed set, matching development. No tuning decision was made from the
sealed test.

Finish with: **use the cheapest effective improvement lever, but measure
quality, latency, and tokens on one deterministic evaluation spine.**

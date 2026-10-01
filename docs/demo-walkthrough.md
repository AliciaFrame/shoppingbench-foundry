# Demo walkthrough

## 1. Frame the problem

Open the root README and introduce the four task families. Emphasize that this
is a behavior benchmark: the model must use tools correctly, not merely produce
a plausible answer.

## 2. Show the data and tools

Open `data/README.md`, then show one row from each source file. Explain the
controlled 3,636-document index and why deterministic distractors make
regressions visible.

Start the tool runtime locally or use the deployed endpoint:

```powershell
uvicorn shoppingbench_foundry.app:app --host 0.0.0.0 --port 8000
```

Demonstrate `find_product`, `view_product_information`, and the explicit
`recommend_product` → `terminate` protocol.

## 3. Show the four agents

Open `agents/product/main.py`, `agents/shop/main.py`,
`agents/voucher/main.py`, and `agents/web/main.py`. Then open the matching
`config/` directories to show how the task behavior is separated while the
runtime remains shared.

## 4. Explain evaluation

Open `src/shoppingbench_foundry/grading.py` and
`evaluations/README.md`. Explain exact IDs, constraints, task invariants, and
why the canonical holdout—not the optimizer judge—controls deployment.

## 5. Show Agent Optimizer

Open `optimization/results/summary.json` and a pair of raw holdout receipts.
Walk through Product and Shop improvements, then use Voucher as the example of
why an optimizer-score improvement is not automatically deployed.

## 6. Show RFT

Open `rft/README.md`, `rft/scripts/prepare_data.py`, and
`rft/scripts/submit.py`. Explain:

- retained optimized instructions become the developer message
- rollouts execute live tools
- the reward is calibrated before spending a job
- canonical holdouts remain untouched
- each task has a distinct suffix

Close with the active Web job receipt and, once available, checkpoint results.

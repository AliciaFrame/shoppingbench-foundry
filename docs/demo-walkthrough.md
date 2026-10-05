# Demo walkthrough

## 1. Frame the problem

Open the root README and introduce the four task families. Emphasize that this
is a behavior benchmark: the model must use tools correctly, not merely produce
a plausible answer.

## 2. Show the data and tools

Open `data/README.md`, then show one row from each source file. Explain the
controlled 3,636-document index and why deterministic distractors make
regressions visible.

Start the tool runtime locally from the repository root or use the deployed
endpoint. The local process requires access to the prepared Azure AI Search
index. Authenticate with `az login`, set `AZURE_SEARCH_ENDPOINT`, and either
configure `SHOPPINGBENCH_API_TOKEN` or allow anonymous access only for an
isolated local demo:

```powershell
python -m pip install -e .
az login

$env:AZURE_SEARCH_ENDPOINT = "https://<search-service>.search.windows.net"
$env:AZURE_SEARCH_INDEX = "shoppingbench-products"
$env:SHOPPINGBENCH_ALLOW_ANONYMOUS = "true"

python -m uvicorn shoppingbench_foundry.app:app `
  --host 127.0.0.1 `
  --port 8000 `
  --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API or
`http://127.0.0.1:8000/health` for a health check. Press `Ctrl+C` to stop the
server. Use `0.0.0.0` only when another machine or container must connect.

For a concrete tool demo, open `GET /find_product`, select **Try it out**, and
use:

| Field | Value |
|---|---|
| `q` | `horsetail violin bow` |
| `page` | `1` |
| `shop_id` | blank |
| `price` | blank |
| `sort` | blank |
| `service` | blank |

The controlled index should return product `3706669986`. Next call
`GET /view_product_information` with `product_ids=3706669986`. When bearer
authentication is enabled, set the `authorization` header to
`Bearer <SHOPPINGBENCH_API_TOKEN>`.

Demonstrate `find_product`, `view_product_information`, and the explicit
`recommend_product` → `terminate` protocol. Swagger exposes the first two as
dedicated GET routes. Recommendation and termination use the generic
`POST /rft/tools/{tool_name}` route: set `tool_name` to `recommend_product` or
`terminate` and provide a `ToolRequest` body. The hosted runtime, rather than
the stateless tool endpoint, records their order in the episode trace.

## 3. Show the four agents

Open `agents/product/main.py`, `agents/shop/main.py`,
`agents/voucher/main.py`, and `agents/web/main.py`. Then open the matching
`config/` directories to show how the task behavior is separated while the
runtime remains shared.

## 4. Explain evaluation

Open `src/shoppingbench_foundry/grading.py` and
`evaluations/README.md`. Explain exact IDs, constraints, task invariants, and
why deterministic task metrics—not the optimizer judge—control deployment.
For v2, checkpoint development and sealed final testing are separate.

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
- product/answer groups do not cross training, development, or sealed test
- each task has a distinct suffix

Label the following as the historical v1 checkpoint story. Close with
`rft/results/web-rft3-holdout-comparison.json`. Emphasize that Step
10 reached `0.901`, while Step 15 fell to `0.836` and the final artifact to
`0.795`; checkpoint selection preserved the gain that final-only deployment
would have lost.

## 7. Tell the hill-climb story

Open `docs/shoppingbench-hill-climb-deck.html` in a browser. The final
cross-task narrative is:

- Product: `0.753 -> 0.961` with Agent Optimizer
- Shop: `0.860 -> 0.998` with Agent Optimizer
- Voucher: `0.898 -> 0.955` with Agent Optimizer, then `0.995` with RFT
- Web: `0.790 -> 0.901` with agentic RFT
- cross-task mean: `0.825 -> 0.964`

Then open `docs/audits/v1-methodology-audit.md`: Voucher's additional RFT gain
was mostly protocol completion, Web contained three product-overlap holdout
cases, and both retained RFT models omitted the required user-facing answer.
Show how v2 fixes those issues before presenting new results.

End on the operating principle: use the cheapest effective improvement lever,
but keep one deterministic evaluation spine across prompt optimization,
training, checkpoint selection, and deployment.

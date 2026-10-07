# Hosted agents

The five agents use Responses-protocol runtimes and separate task entry points:

| Directory | Behavior |
|---|---|
| `product/` | Recommend one product satisfying all constraints |
| `shop/` | Recommend all requested products from one shop |
| `voucher/` | Apply voucher scope, threshold, discount, and budget |
| `web/` | Resolve a factual clue and use it in product search |
| `web_search/` | Resolve a clue with native web search, then select a catalog product |

Each task directory contains:

- `main.py`: explicit hosted-agent entry point
- `baseline/`: minimal off-the-shelf instructions, skills, and tools
- `optimized/`: retained optimized instructions, skills, and tools
- `.foundry/agent-metadata.yaml`: local evaluation context

The shared runtime in `shared/runtime.py`:

1. Loads the task configuration through the Agent Optimizer config contract.
2. Calls the configured model deployment through the Responses API.
3. Executes live ShoppingBench search/detail tools.
4. Records the exact function-call trace.
5. Allows at most 12 sequential model/tool steps and requires one tool call at
   a time, so a single turn cannot bypass the bound with parallel searches.
6. Exposes only `terminate` after recommendation, then makes a separate
   tool-free request for the grounded user-facing answer.
7. Returns structured IDs, termination state, assistant text, and usage.

## Deploy

Set the values documented in `.env.example`, including the separate model
deployment and endpoint pair for each task, then:

```powershell
Set-Location agents
azd deploy
```

The deployment manifest is environment-neutral: model, project, tool endpoint,
and token values come from the active environment rather than committed files.
Model settings are task-specific because base and fine-tuned deployments can
use different endpoints and authentication scopes. The runtime automatically
uses the Cognitive Services scope for classic Azure OpenAI endpoints and the
Foundry scope for project endpoints.

`OPTIMIZATION_LOCAL_DIR` is task-qualified in `azure.yaml` (for example,
`voucher/optimized`). The shared runtime resolves relative configuration paths
from the packaged agents root, so the same path works from a repository clone
and from the `/app/<task>` hosted layout.

## Local import check

Run this from the repository root after setting the model and tool environment
variables:

```powershell
Set-Location ..
python -c "from agents.shared.runtime import TASK; print(TASK)"
```

Live startup additionally requires model and tool endpoint configuration.

`web_search/` uses `shared/web_search_runtime.py` rather than the catalog-only
shared runtime. It first calls the Responses API native web-search tool,
captures cited evidence, and then performs the bounded catalog-selection
trajectory. Its retained optimized configuration preserves a structured
terminal answer containing the product ID, resolved clue, and complete
grounded recommendation.

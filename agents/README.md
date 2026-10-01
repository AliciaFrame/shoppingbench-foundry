# Hosted agents

The four agents use one shared Responses-protocol runtime and separate task
entry points:

| Directory | Behavior |
|---|---|
| `product/` | Recommend one product satisfying all constraints |
| `shop/` | Recommend all requested products from one shop |
| `voucher/` | Apply voucher scope, threshold, discount, and budget |
| `web/` | Resolve a factual clue and use it in product search |

Each task directory contains:

- `main.py`: explicit hosted-agent entry point
- `config/instructions.md`: retained instructions
- `config/tools.json`: retained tool definitions
- `config/skills/`: retained task skill, when used
- `.foundry/agent-metadata.yaml`: local evaluation context

The shared runtime in `shared/runtime.py`:

1. Loads the task configuration through the Agent Optimizer config contract.
2. Calls MAI-Code-1.1-Flash through the Responses API.
3. Executes live ShoppingBench search/detail tools.
4. Records the exact function-call trace.
5. Returns structured IDs, termination state, assistant text, and usage.

## Deploy

Set the values documented in `.env.example`, then:

```powershell
Set-Location agents
azd deploy
```

The deployment manifest is environment-neutral: model, project, tool endpoint,
and token values come from the active environment rather than committed files.

## Local import check

Run this from the repository root after setting the model and tool environment
variables:

```powershell
Set-Location ..
python -c "from agents.shared.runtime import TASK; print(TASK)"
```

Live startup additionally requires model and tool endpoint configuration.

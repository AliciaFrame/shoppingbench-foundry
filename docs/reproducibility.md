# Reproducibility

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agents,dev]"
```

Cloud evaluation also requires the environment variables documented in
`.env.example`, including the model endpoint, task model deployment, tool
endpoint, and API token.

The commands below separate three levels of reproduction:

1. deterministic local generation and tests;
2. live evaluation against compatible model and tool deployments;
3. optional Agent Optimizer and RFT runs that require separate Foundry access.

## Regenerate deterministic artifacts

```powershell
python data\scripts\prepare.py
python rft\scripts\prepare_data.py
git diff --exit-code -- data\prepared evals\datasets rft\data
```

Expected split sizes per task are 20 optimizer round-one cases, 40 optimizer
round-two cases, 30 development cases, and 50 final-test cases. RFT uses 20
validation cases; Product, Shop, and Voucher have 150 training cases, while Web
has 50.

## Evaluate off-the-shelf behavior

Start or deploy the tool API first, then set the live endpoints. `.env.example`
is a reference file; scripts read the process environment and do not load it
automatically.

```powershell
$env:MAI_OPENAI_BASE_URL = "https://<model-endpoint>/openai/v1"
$env:MAI_MODEL_DEPLOYMENT_NAME = "<model-deployment>"
$env:SHOPPINGBENCH_TOOL_URL = "https://<deployed-tool-api>"
$env:SHOPPINGBENCH_API_TOKEN = "<tool-api-token>"
$task = "product"
$env:SHOPPINGBENCH_TASK = $task
$env:OPTIMIZATION_LOCAL_DIR = "$task/baseline"
python evals\scripts\evaluate_agent.py `
  --runtime-module agents.shared.baseline_runtime `
  --dataset "evals\datasets\development\$task.jsonl" `
  --documents data\prepared\search-documents.jsonl `
  --output ".foundry\results\$task-baseline.jsonl" `
  --rollouts 3 `
  --workers 1
```

The baseline runtime intentionally leaves all tools visible, does not require
tool use, does not force `recommend_product -> terminate`, and does not inject
a grounded final-answer turn.

## Evaluate the optimized agent

```powershell
$env:OPTIMIZATION_LOCAL_DIR = "$task/optimized"
python evals\scripts\evaluate_agent.py `
  --runtime-module agents.shared.runtime `
  --dataset "evals\datasets\development\$task.jsonl" `
  --documents data\prepared\search-documents.jsonl `
  --output ".foundry\results\$task-optimized.jsonl" `
  --rollouts 3 `
  --workers 1
```

For Live Web Search, use the native-search runtime and its dedicated
configuration:

```powershell
$env:MAI_MODEL_DEPLOYMENT_NAME = "<web-search-capable-deployment>"
$env:OPTIMIZATION_LOCAL_DIR = "web_search/optimized"
$env:AGENT_MODE = "optimized"
python evals\scripts\evaluate_agent.py `
  --runtime-module agents.shared.web_search_runtime `
  --dataset evals\datasets\development\web.jsonl `
  --documents data\prepared\search-documents.jsonl `
  --output .foundry\results\web-search-optimized.jsonl `
  --rollouts 3 `
  --workers 1
```

## Compare stages

```powershell
python evals\scripts\summarize_development.py `
  --baseline ".foundry\results\$task-baseline.jsonl" `
  --candidate ".foundry\results\$task-optimized.jsonl" `
  --output ".foundry\results\$task-comparison.json"
```

The summary pairs rollouts by case and reports case-clustered confidence
intervals so repeated rollouts are not treated as independent cases.

Regenerate the published aggregate table from committed receipts:

```powershell
python evals\scripts\build_release_summary.py
git diff --exit-code -- evals\results\summary.json
```

## Run Agent Optimizer

```powershell
python optimize\run.py product `
  --round 1 `
  --agent-version <version> `
  --eval-model <judge-deployment> `
  --optimization-model <optimizer-deployment> `
  --dry-run
```

Round one uses `agents/product/baseline`; round two uses
`optimize/seeds/product/round2`. Remove `--dry-run` only after inspecting the
materialized config under `optimize/work/`.

## Prepare and calibrate RFT

```powershell
python rft\scripts\prepare_data.py

python -m rft.scripts.calibrate `
  --results <calibration-results.jsonl> `
  --dataset rft\data\voucher-calibration-eval.jsonl `
  --output .foundry\voucher-calibration.json
```

Do not train unless calibration has at least 60 observations, at least 20
cases, at least three rollouts per case, and a 25-50% failure rate at the
selected threshold.

RFT submission is billable:

```powershell
python -m rft.scripts.submit voucher `
  --calibration .foundry\voucher-calibration.json `
  --data-dir rft\data `
  --grader-version v3 `
  --confirm-submit `
  --output .foundry\voucher-submission.json
```

For hardened Catalog Web, calibrate and submit with the v5 grader:

```powershell
python -m rft.scripts.calibrate `
  --results rft\jobs\web-v5\base-calibration-results.jsonl `
  --dataset rft\data\web-validation-eval.jsonl `
  --grader-version web-v5 `
  --output .foundry\web-v5-calibration.json

python -m rft.scripts.submit web `
  --calibration .foundry\web-v5-calibration.json `
  --data-dir rft\data `
  --grader-version web-v5 `
  --eval-interval 2 `
  --confirm-submit `
  --output .foundry\web-v5-submission.json
```

Evaluate every deployable checkpoint on the development split before opening
the final test. A numerically highest final checkpoint is not automatically
selected.

Live Web Search RFT is not part of the published workflow. Its current
production result is the Agent Optimizer configuration.

The receipts in `rft/jobs/` contain IDs from the published runs. Do not copy
those IDs into a new environment; new submissions create new job and file IDs.

## Validate the repository

```powershell
python -m pytest
python -m ruff check .
az bicep build --file azure\infra\main.bicep --stdout | Out-Null
git diff --check
```

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
(static) has 50. Web (live) uses 38 training and 21 validation rows prepared
from captured web evidence.

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

## Evaluate Web (live)

Web (live) uses its own harness, which records every model response, the
captured web evidence, and every executed tool call. It defaults to the
settings behind the published results (8 tool steps, medium reasoning,
256-token web lookup with two cited sources).

```powershell
$env:MAI_OPENAI_BASE_URL = "https://<mai-endpoint>/openai/v1"
$env:MAI_MODEL_DEPLOYMENT_NAME = "<mai-code-1.1-flash-deployment>"
$env:WEB_SEARCH_OPENAI_BASE_URL = "https://<web-search-endpoint>/openai/v1"
$env:WEB_SEARCH_MODEL_DEPLOYMENT_NAME = "<gpt-5.4-mini-deployment>"
$env:SHOPPINGBENCH_TOOL_URL = "https://<deployed-tool-api>"
$env:SHOPPINGBENCH_API_TOKEN = "<tool-api-token>"
foreach ($mode in "baseline", "optimized") {
  python evals\scripts\evaluate_live_web.py `
    --agent-mode $mode `
    --dataset evals\datasets\development\web.jsonl `
    --output ".foundry\results\web-search-$mode.jsonl"
}
```

The web-search lookup and the catalog agent must use separate endpoints so
their token usage is accounted separately. For the RFT stage, point
`MAI_MODEL_DEPLOYMENT_NAME` at the deployed fine-tuned model and use
`--agent-mode optimized`.

Score receipts with the published Web (live) RFT grader and compare stages:

```powershell
$env:RFT_GRADER_MODEL_DEPLOYMENT_NAME = "<gpt-5.4-judge-deployment>"
python evals\scripts\score_live_web.py `
  --results .foundry\results\web-search-optimized.jsonl `
  --dataset evals\datasets\development\web.jsonl `
  --output .foundry\results\web-search-optimized.scored.jsonl `
  --baseline-details .foundry\results\web-search-baseline.scored.jsonl
```

The judge uses `WEB_SEARCH_OPENAI_BASE_URL`. Canonical-score comparisons use
`summarize_development.py` exactly as for the other agents.

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

For hardened Web (static), calibrate and submit with the v5 grader:

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

### Web (live)

The RFT data pairs each training query with web evidence captured once by the
fixed lookup step. Regenerate it from the committed captures and review:

```powershell
python -m rft.scripts.prepare_live_web_data `
  --evidence rft\data\web-search\captures.jsonl `
  --review rft\data\web-search\review.json `
  --output-dir rft\data\web-search
git diff --exit-code -- rft\data\web-search
```

The review excludes 11 cases with ambiguous premises, insufficient evidence,
or connected-group leakage; see `rft/data/web-search/manifest.json`.

Calibrate the grader on frozen base-model rollouts (three per case). The
published rollouts are in `rft/jobs/web-search-v6/`:

```powershell
$env:RFT_GRADER_MODEL_DEPLOYMENT_NAME = "<gpt-5.4-judge-deployment>"
$env:WEB_SEARCH_OPENAI_BASE_URL = "https://<judge-endpoint>/openai/v1"
foreach ($split in "training", "validation") {
  $data = if ($split -eq "training") { "web-train" } else { "web-validation" }
  python -m rft.scripts.recalibrate_live_web `
    --results "rft\jobs\web-search-v6\calibration-results-$split.jsonl" `
    --dataset "rft\data\web-search\$data.jsonl" `
    --output-dir ".foundry\web-search-v6-$split"
}
```

The training calibration sets the reward parameters: 28.9% of training
rollouts fail, inside the 25-50% target. `submit.py` binds the submission to
the validation calibration (it checks the hash of `web-validation.jsonl`), and
that split fails 19.0% of rollouts. The published run therefore relied on the
training-split band rather than the validation split, which is reported for
transparency.

The hosted grader endpoint (`/grade/web-search/v6`) needs the same judge
settings, and enough judge capacity for concurrent training rollouts. The
published run used 545k tokens per minute; 20k caused the trainer to abort on
throttled grading calls.

Submit with the settings used for the published run:

```powershell
python -m rft.scripts.submit web `
  --grader-version web-search-v6 `
  --calibration .foundry\web-search-v6-validation\calibration.json `
  --data-dir rft\data\web-search `
  --n-epochs 3 --batch-size 8 --learning-rate-multiplier 0.5 `
  --eval-interval 3 --eval-samples 21 --max-episode-steps 8 `
  --compute-multiplier 1.5 --reasoning-effort medium `
  --confirm-submit `
  --output .foundry\web-search-v6-submission.json
```

The receipts in `rft/jobs/` contain IDs from the published runs. Do not copy
those IDs into a new environment; new submissions create new job and file IDs.

## Validate the repository

```powershell
python -m pytest
python -m ruff check .
az bicep build --file azure\infra\main.bicep --stdout | Out-Null
git diff --check
```

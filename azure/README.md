# Azure setup

The Azure layer hosts the product catalog and the live tool/grader contract.

## Resources

- Azure AI Search: product retrieval and detail lookup
- Azure Container Apps: HTTPS tool and grader API
- Azure Container Registry: runtime image
- Azure Storage: benchmark artifacts
- User-assigned managed identity: runtime access
- Application Insights + Log Analytics: telemetry

The Bicep template uses managed identity, disables Search local authentication,
disables Storage shared-key access, requires TLS 1.2, and stores the API bearer
token as a Container Apps secret.

## Provision

```powershell
azd auth login
azd env new shoppingbench-demo
azd env set SHOPPINGBENCH_API_TOKEN "<generated-secret>"
azd provision
azd deploy
```

Before deploying the image, confirm that the runtime identity's `AcrPull`
assignment is visible on the new registry. Provisioning and deployment are
separate steps so this permission has time to propagate.

For a small demo rather than concurrent benchmark/training traffic, set
`azd env set SHOPPINGBENCH_DEPLOYMENT_PROFILE demo` before provisioning. This
uses Basic Search and one 0.25-vCPU / 0.5-GiB Container App replica. The default
`benchmark` profile retains Standard Search and 2-5 larger replicas. Search and
ACR incur charges while provisioned, even when the demo is idle.

Set both `AZURE_LOCATION` and `AZURE_SEARCH_LOCATION` in the AZD environment
when choosing a region other than Sweden Central.

The deployment outputs:

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_INDEX`
- `SHOPPINGBENCH_TOOL_URL`
- ACR and Storage resource names

## Load the search index

The caller must have `Search Service Contributor` to create the index and
`Search Index Data Contributor` to upload documents on the Search service.

```powershell
python -m shoppingbench_foundry.index_documents `
  --endpoint $env:AZURE_SEARCH_ENDPOINT `
  --documents data\prepared\search-documents.jsonl
```

The Container App identity receives only `Search Index Data Reader`.

## Run a small demo

The repository has an API and agent/evaluation scripts, not a graphical shopping
frontend. A bounded Product demonstration can use the deployed tool API and an
existing MAI deployment without deploying hosted agents or starting optimization
or training.

Configure the active AZD environment with `MAI_OPENAI_BASE_URL`,
`MAI_MODEL_DEPLOYMENT_NAME`, `SHOPPINGBENCH_TOOL_URL`, and the API token, then:

```powershell
azd env set SHOPPINGBENCH_TASK product
azd env set OPTIMIZATION_LOCAL_DIR product/optimized
$output = ".foundry\results\demo-product-$(Get-Date -Format yyyyMMdd-HHmmss).jsonl"
azd exec --shell cmd ".\.venv\Scripts\python.exe evals\scripts\evaluate_agent.py --runtime-module agents.shared.runtime --dataset evals\datasets\development\product.jsonl --documents data\prepared\search-documents.jsonl --output $output --limit 1 --rollouts 1 --workers 1"
```

`azd exec` loads the active environment without printing its secrets. On Windows,
use the explicit `cmd` shell and a single command string; passing the Python
executable as a script path can make AZD invoke it as a PowerShell script.
Each run writes its trajectory, grade, usage, and contract manifest to the
ignored results directory. The example uses a development case, not the sealed
final-test split, and makes billable model calls.

The root `.dockerignore` limits remote image-build context to the runtime's
package files, excluding local environments, credentials, and benchmark data.
When building without Docker installed locally, use `az acr build` and deploy
its resulting image with `azd deploy shoppingbench-api --from-package <image>`.

## Runtime endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /find_product` | Search |
| `GET /view_product_information` | Detail inspection |
| `POST /rft/tools/{tool_name}` | Foundry RFT function-call adapter |
| `POST /grade/v3` | Canonical RFT endpoint grader |
| `POST /grade/web/v5` | Web (static) RFT endpoint grader |
| `POST /grade/web-search/v6` | Web (live) RFT endpoint grader |

Set `SHOPPINGBENCH_API_TOKEN` to require bearer authentication on all tool and
grader calls.

The Web (live) grader calls a GPT-5.4 judge. Before provisioning, set
`WEB_SEARCH_OPENAI_BASE_URL`, `WEB_SEARCH_MODEL_DEPLOYMENT_NAME`, and
`RFT_GRADER_MODEL_DEPLOYMENT_NAME` with `azd env set`, and grant the Container
App identity `Cognitive Services OpenAI User` on that model resource. Give the
judge enough capacity for concurrent training rollouts; the published run used
545k tokens per minute.

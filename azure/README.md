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
azd up
```

The deployment outputs:

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_INDEX`
- `SHOPPINGBENCH_TOOL_URL`
- ACR and Storage resource names

## Load the search index

The caller must have `Search Index Data Contributor` on the Search service.

```powershell
python -m shoppingbench_foundry.index_documents `
  --endpoint $env:AZURE_SEARCH_ENDPOINT `
  --documents data\prepared\search-documents.jsonl
```

The Container App identity receives only `Search Index Data Reader`.

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

# Architecture

## Data plane

The tool runtime is a stateless FastAPI application on Azure Container Apps.
It reads products from Azure AI Search through managed identity and exposes
search, detail, RFT tool-adapter, and grader endpoints.

The controlled corpus is generated deterministically from every gold product
referenced by the frozen benchmark. Each gold product receives one distractor
that deliberately violates a relevant constraint.

## Agent plane

Five Microsoft Foundry hosted agents load separate instructions, skills, and
tool definitions. Product, Shop, Voucher, and Catalog Web share the bounded
catalog runtime. Live Web Search first uses the native Responses API
`web_search` tool, then passes the grounded evidence into the catalog-selection
runtime. Both paths continue multi-step function calls with
`previous_response_id`.

## Evaluation plane

The deterministic grader retrieves recommended products and scores exact IDs,
constraint satisfaction, task invariants, process completion, and grounded
final answers. Development sets control optimization and checkpoint decisions;
the sealed final sets remain outside optimizer and RFT training data.

## Improvement plane

Agent Optimizer searches configuration changes around a fixed model. Published
Agentic RFT trains Voucher and hardened Catalog Web policies against live
catalog-tool episodes. Live Web Search currently stops at the retained Agent
Optimizer configuration; its RFT work is not part of the published workflow.

## Security

- No credentials are committed.
- Azure resources use managed identity where supported.
- Search local authentication and Storage shared-key access are disabled.
- Tool and grader endpoints support bearer authentication.
- Model and RFT credentials are read only from process environment.

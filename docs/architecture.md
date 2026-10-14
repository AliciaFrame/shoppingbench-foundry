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
tool definitions. Every agent runs on MAI-Code-1.1-Flash. Product, Shop,
Voucher, and Web (static) share the bounded catalog runtime. Web (live) first
runs a fixed web-search lookup (the Responses API `web_search` tool on
GPT-5.4-mini), then passes the cited evidence to the MAI-Code-1.1-Flash
catalog agent. The lookup step is identical across stages; only the catalog
agent is optimized and fine-tuned. Both paths continue multi-step function
calls with `previous_response_id`.

## Evaluation plane

The deterministic grader retrieves recommended products and scores exact IDs,
constraint satisfaction, task invariants, process completion, and grounded
final answers. Development sets control optimization and checkpoint decisions;
the sealed final sets remain outside optimizer and RFT training data.

## Improvement plane

Agent Optimizer searches configuration changes around a fixed model. Agentic
RFT trains Voucher, Web (static), and Web (live) policies against live
catalog-tool episodes, scored by graders hosted on the same API. Web (live)
uses a semantic grader: an LLM judge checks clue correctness and grounding
against captured evidence, deterministic gates check the exact product and
protocol, and a cost term rewards succinct trajectories.

## Security

- No credentials are committed.
- Azure resources use managed identity where supported.
- Search local authentication and Storage shared-key access are disabled.
- Tool and grader endpoints support bearer authentication.
- Model and RFT credentials are read only from process environment.

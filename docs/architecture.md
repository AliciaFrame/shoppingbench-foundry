# Architecture

## Data plane

The tool runtime is a stateless FastAPI application on Azure Container Apps.
It reads products from Azure AI Search through managed identity and exposes
search, detail, RFT tool-adapter, and grader endpoints.

The controlled corpus is generated deterministically from every gold product
referenced by the frozen benchmark. Each gold product receives one distractor
that deliberately violates a relevant constraint.

## Agent plane

Four Microsoft Foundry hosted agents share one implementation but load separate
instructions, skills, and tool definitions. They use the Responses protocol so
multi-step function calls can be continued with `previous_response_id`.

## Evaluation plane

The canonical grader retrieves recommended products and scores exact IDs,
constraint satisfaction, task invariants, and process completion. Fixed
holdouts remain outside optimizer and RFT training data.

## Improvement plane

Agent Optimizer searches configuration changes around a fixed model. Agentic
RFT trains the model policy against live tool episodes. Keeping the mechanisms
separate makes their contribution measurable and allows the final system to
combine the best model checkpoint with the best agent configuration.

## Security

- No credentials are committed.
- Azure resources use managed identity where supported.
- Search local authentication and Storage shared-key access are disabled.
- Tool and grader endpoints support bearer authentication.
- RFT API keys are read only from process environment.

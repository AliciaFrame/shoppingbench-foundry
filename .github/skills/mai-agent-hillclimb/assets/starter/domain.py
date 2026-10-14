"""Domain hook shared by inference and training tool endpoints."""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "lookup_policy",
        "description": "Look up the severity rule for an incident category.",
        "parameters": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": ["outage", "billing", "other"]}},
            "required": ["category"],
            "additionalProperties": False,
        },
    }
]


def execute_tool(name, arguments):
    if name != "lookup_policy":
        raise ValueError(f"Unknown tool: {name}")
    category = arguments["category"]
    policies = {
        "outage": {"severity": "critical", "team": "operations"},
        "billing": {"severity": "normal", "team": "finance"},
        "other": {"severity": "normal", "team": "support"},
    }
    if category not in policies:
        raise ValueError(f"Unknown category: {category}")
    return policies[category]

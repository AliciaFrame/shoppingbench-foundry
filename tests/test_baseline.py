from typing import ClassVar

from shoppingbench_foundry.baseline import run_episode


class FakeResponse:
    id = "response-1"
    output: ClassVar[list] = []
    output_text = "done"
    usage = None


class FakeResponses:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_baseline_uses_model_default_sampling_parameters():
    client = FakeClient()

    result = run_episode(
        client=client,
        model="mai-code-1-1-flash-base",
        query="Find a calculator",
        tool_url="http://localhost:8000",
        token=None,
        max_steps=1,
    )

    assert result["response_id"] == "response-1"
    assert "temperature" not in client.responses.requests[0]

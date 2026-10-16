import json

import httpx
import pytest

from app.qualification.extractors import ExtractionError, LLMExtractor


def response(facts):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(facts)}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }


async def test_structured_output_contract_and_usage():
    facts = {
        "category": "unknown",
        "category_evidence": None,
        "users": None,
        "budget": None,
        "deadline": None,
        "integrations": [],
        "warnings": [],
    }

    def handler(request):
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["model"] == "test-model"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=response(facts))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await LLMExtractor(http, "https://llm.test/v1", "test-key", "test-model").extract(
            "Привет"
        )
    assert result.token_usage["total_tokens"] == 70


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        response({"category": "unknown"}),
        response(
            {
                "category": "support",
                "category_evidence": "несуществующая цитата",
                "users": None,
                "budget": None,
                "deadline": None,
                "integrations": [],
                "warnings": [],
            }
        ),
    ],
)
async def test_invalid_or_unsupported_output_never_enters_pipeline(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload),
        )
    ) as http:
        with pytest.raises(ExtractionError):
            await LLMExtractor(http, "https://llm.test/v1", "key", "model").extract("Привет")

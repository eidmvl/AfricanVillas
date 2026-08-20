import asyncio
from types import SimpleNamespace
from typing import Any

from african_villas.analysis import (
    authenticate_codex_client,
    codex_output_schema,
    extract_json_object,
)
from african_villas.codex_auth_cli import _account_payload


class _FakeCodex:
    def __init__(self) -> None:
        self.api_keys: list[str] = []

    async def login_api_key(self, api_key: str) -> None:
        self.api_keys.append(api_key)


class _AccountRoot:
    type = "chatgpt"
    plan_type = SimpleNamespace(value="plus")


class _AccountResponse:
    account = SimpleNamespace(root=_AccountRoot())


def test_codex_account_status_hides_identity() -> None:
    assert _account_payload(_AccountResponse()) == {
        "authenticated": True,
        "type": "chatgpt",
        "plan": "plus",
    }


def test_explicit_api_key_auth_for_unattended_codex(monkeypatch) -> None:
    codex = _FakeCodex()
    monkeypatch.setenv("AFRICAN_VILLAS_CODEX_AUTH_MODE", "api")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    asyncio.run(authenticate_codex_client(codex))

    assert codex.api_keys == ["test-key"]


def test_codex_keeps_chatgpt_subscription_login(monkeypatch) -> None:
    codex = _FakeCodex()
    monkeypatch.setenv("AFRICAN_VILLAS_CODEX_AUTH_MODE", "chatgpt")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    asyncio.run(authenticate_codex_client(codex))

    assert codex.api_keys == []


def test_api_auth_requires_key(monkeypatch) -> None:
    codex = _FakeCodex()
    monkeypatch.setenv("AFRICAN_VILLAS_CODEX_AUTH_MODE", "api")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    try:
        asyncio.run(authenticate_codex_client(codex))
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("Missing API key must fail in api mode")


def test_extract_json_from_code_fence() -> None:
    payload = extract_json_object('```json\n{"country": "Танзания"}\n```')
    assert payload == {"country": "Танзания"}


def test_codex_output_schema_forbids_extra_object_fields() -> None:
    schema = codex_output_schema()
    assert schema["additionalProperties"] is False
    source_schema = schema["$defs"]["EvidenceSource"]
    assert "format" not in source_schema["properties"]["url"]
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_codex_output_schema_requires_every_property() -> None:
    schema = codex_output_schema()

    def assert_required(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert node["required"] == list(properties)
                assert "default" not in node
            for value in node.values():
                assert_required(value)
        elif isinstance(node, list):
            for value in node:
                assert_required(value)

    assert_required(schema)

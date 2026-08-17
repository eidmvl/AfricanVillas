from typing import Any

from african_villas.analysis import codex_output_schema, extract_json_object


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

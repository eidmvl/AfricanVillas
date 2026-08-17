from african_villas.models import Block1Row, input_fingerprint


def test_fingerprint_is_normalized() -> None:
    first = input_fingerprint(" Танзания ", "Занзибар", "VILLAS_FOR_SALE")
    second = input_fingerprint("танзания", "  занзибар ", "VILLAS_FOR_SALE")
    assert first == second


def test_missing_fields_distinguishes_empty_and_partial() -> None:
    row = Block1Row(
        id=1,
        project_id=1,
        position=1,
        country="Танзания",
        region="",
        goal_code="",
        map_url="",
        user_note="",
        input_hash="",
        status="draft",
        error_message="",
        created_at="",
        updated_at="",
        calculated_at=None,
    )
    assert not row.is_empty
    assert row.missing_fields() == ["регион", "цель проекта"]


from linux_voice_assistant.ha_tools.client import EntityRecord, _entity_match_score, _matches_area, _matches_query, _suggested_services


def test_matches_query_uses_name_area_and_entity_id():
    entity = EntityRecord(
        entity_id="light.kitchen_ceiling",
        name="Kitchen Ceiling",
        state="on",
        domain="light",
        area="Kitchen",
        attributes={"friendly_name": "Kitchen Ceiling"},
    )

    assert _matches_query(entity, "kitchen")
    assert _matches_query(entity, "ceiling")
    assert _matches_query(entity, "kitchen ceiling")
    assert not _matches_query(entity, "bathroom")


def test_matches_query_handles_plural_and_domain_tokens():
    entity = EntityRecord(
        entity_id="light.office_desk",
        name="Desk Lamp",
        state="on",
        domain="light",
        area="Office",
        attributes={"friendly_name": "Desk Lamp"},
    )

    assert _matches_query(entity, "office lights")
    assert _entity_match_score(entity, "office lights") > 0


def test_entity_result_includes_suggested_services_for_actionable_domains():
    entity = EntityRecord(
        entity_id="script.play_music_in_salon",
        name="Play music in Salon",
        state="off",
        domain="script",
        area=None,
        attributes={"friendly_name": "Play music in Salon"},
    )

    result = entity.as_tool_result()

    assert result["suggested_service_domain"] == "script"
    assert result["suggested_services"] == ["turn_on"]
    assert _suggested_services("light") == ["turn_on", "turn_off", "toggle"]


def test_matches_area_uses_generic_token_matching():
    assert _matches_area("Living Room", "living room")
    assert not _matches_area("Office", "kitchen")

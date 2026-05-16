from linux_voice_assistant.ha_tools.client import (
    EntityRecord,
    _entity_match_score,
    _matches_area,
    _matches_query,
    _service_request_path,
    _suggested_services,
)
from linux_voice_assistant.ha_tools.settings_listener import _parse_entity_state
from linux_voice_assistant.tools.web_search import extract_duckduckgo_results


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
    assert _suggested_services("todo") == [
        "add_item",
        "update_item",
        "remove_item",
        "remove_completed_items",
        "get_items",
    ]


def test_todo_get_items_requests_home_assistant_service_response():
    assert _service_request_path("todo", "get_items") == "/api/services/todo/get_items?return_response"
    assert _service_request_path("todo", "add_item") == "/api/services/todo/add_item"


def test_matches_area_uses_generic_token_matching():
    assert _matches_area("Living Room", "living room")
    assert not _matches_area("Office", "kitchen")


def test_extract_duckduckgo_results_parses_titles_urls_and_snippets():
    page = '''
    <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fnews">Example Result</a>
    <a class="result__snippet">Example snippet text</a>
    '''

    results = extract_duckduckgo_results(page, max_results=5)

    assert results == [{"title": "Example Result", "url": "https://example.com/news", "snippet": "Example snippet text"}]


def test_settings_listener_parses_integer_number_states():
    assert _parse_entity_state("memory_interactions_count", {"state": "6.0"}) == 6


def test_settings_listener_ignores_unavailable_setting_states():
    assert _parse_entity_state("openai_instructions", {"state": "unavailable"}) is None
    assert _parse_entity_state("openai_instructions", {"state": "unknown"}) is None

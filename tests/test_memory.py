from linux_voice_assistant.memory import InteractionMemoryStore


def test_interaction_memory_store_persists_recent_pairs(tmp_path):
    store = InteractionMemoryStore(tmp_path / "interaction_memory.json", max_stored=3)

    store.append(user="first user", assistant="first assistant")
    store.append(user="second user", assistant="second assistant")
    store.append(user="third user", assistant="third assistant")
    store.append(user="fourth user", assistant="fourth assistant")

    recent = store.load_recent(2)

    assert [interaction.user for interaction in recent] == ["third user", "fourth user"]
    assert [interaction.assistant for interaction in recent] == ["third assistant", "fourth assistant"]


def test_interaction_memory_store_ignores_empty_pairs(tmp_path):
    store = InteractionMemoryStore(tmp_path / "interaction_memory.json")

    store.append(user="", assistant="answer")

    assert store.load_recent(10) == []

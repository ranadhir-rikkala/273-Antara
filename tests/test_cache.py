from agent.cache import groups_key


def test_groups_key_is_order_and_duplicate_insensitive() -> None:
    assert groups_key(["managers", "employees", "employees"]) == "employees,managers"
    assert groups_key([]) == ""

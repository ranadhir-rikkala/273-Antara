from agent.tools import schemas


def test_schemas_can_be_narrowed_by_name() -> None:
    assert len(schemas()) == 3
    assert [tool["function"]["name"] for tool in schemas(["request_access"])] == ["request_access"]


def test_tool_permissions_by_group() -> None:
    from agent.tools import may_use

    assert may_use("request_access", ["employees"]) is True
    assert may_use("request_access", ["contractors"]) is False
    assert may_use("create_ticket", ["contractors"]) is True
    assert may_use("drop_tables", ["managers"]) is False

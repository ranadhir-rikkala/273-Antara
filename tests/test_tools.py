from agent.tools import schemas


def test_schemas_can_be_narrowed_by_name() -> None:
    assert len(schemas()) == 3
    assert [tool["function"]["name"] for tool in schemas(["request_access"])] == ["request_access"]

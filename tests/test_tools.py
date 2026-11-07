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


def test_missing_and_blank_required_arguments_are_reported() -> None:
    from agent.tools import REGISTRY, prepare_arguments

    args, missing = prepare_arguments(REGISTRY["request_access"], {"system": " ", "role": "read"}, "I need access")
    assert missing == ["system"]


def test_invalid_choices_are_inferred_from_the_question_or_reported() -> None:
    from agent.tools import REGISTRY, prepare_arguments

    spec = REGISTRY["request_access"]
    args, missing = prepare_arguments(
        spec, {"system": "billing db", "role": "employee"}, "I need write access to billing"
    )
    assert args["role"] == "write" and missing == []
    args, missing = prepare_arguments(spec, {"system": "billing db", "role": "employee"}, "I need access to billing")
    assert missing == ["role"]


def test_a_ticket_needs_the_user_to_describe_the_problem() -> None:
    from agent.tools import REGISTRY, prepare_arguments

    spec = REGISTRY["create_ticket"]
    model_guess = {"title": "Ticket", "description": "User wants a ticket"}
    assert prepare_arguments(spec, model_guess, "Please create a ticket")[1] == ["description"]
    real = {"title": "Keyboard", "description": "Keys stick"}
    assert prepare_arguments(spec, real, "Please create a ticket: my keyboard keys keep sticking")[1] == []
    assert (
        prepare_arguments(spec, {"title": "x", "description": "y", "priority": "urgent"}, "my keyboard keys stick")[0][
            "priority"
        ]
        == "medium"
    )


def test_clarifying_questions_name_what_is_missing() -> None:
    from agent.tools import REGISTRY, clarifying_question

    assert "read or write" in clarifying_question(REGISTRY["request_access"], ["role"])
    assert "Which system" in clarifying_question(REGISTRY["request_access"], ["system", "role"])

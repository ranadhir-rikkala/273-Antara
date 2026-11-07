from agent.summary import summarize_tool_result


def test_ticket_and_access_confirmations_come_from_tool_output() -> None:
    assert summarize_tool_result("create_ticket", {"ticket_id": "t-1", "status": "OPEN"}) == (
        "I created ticket t-1 for you. Its status is OPEN."
    )
    assert summarize_tool_result("request_access", {"system": "prod-db", "role": "read"}, "carol") == (
        "Your read access to prod-db was granted after carol approved it."
    )
    assert "couldn't find" in summarize_tool_result("get_ticket_status", {"found": False})

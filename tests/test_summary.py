from agent.summary import summarize_tool_result


def test_ticket_and_access_confirmations_come_from_tool_output() -> None:
    assert summarize_tool_result("create_ticket", {"ticket_id": "t-1", "status": "OPEN"}) == (
        "I created ticket t-1 for you. Its status is OPEN."
    )
    assert summarize_tool_result("request_access", {"system": "prod-db", "role": "read"}, "carol") == (
        "Your read access to prod-db was granted after carol approved it."
    )
    assert "couldn't find" in summarize_tool_result("get_ticket_status", {"found": False})


def test_found_ticket_status_and_unknown_tools_are_summarized() -> None:
    found = {"found": True, "ticket_id": "t-1", "title": "Screen lock", "status": "OPEN"}
    assert summarize_tool_result("get_ticket_status", found) == "Ticket t-1 (Screen lock) is OPEN."
    assert summarize_tool_result("request_access", {"system": "vpn", "role": "user"}) == (
        "Your user access to vpn was granted."
    )
    assert summarize_tool_result("future_tool", {}) == "Done: future_tool finished."

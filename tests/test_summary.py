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


def test_access_confirmations_say_when_the_access_ends() -> None:
    granted = {"system": "prod-db", "role": "read", "lease_seconds": 30 * 86400}
    assert summarize_tool_result("request_access", granted, "carol") == (
        "Your read access to prod-db was granted after carol approved it. It ends automatically in 30 days."
    )
    assert summarize_tool_result("request_access", {**granted, "lease_seconds": 86400}).endswith("in 1 day.")
    assert summarize_tool_result("request_access", {**granted, "lease_seconds": 7200}).endswith("in 2 hours.")
    assert summarize_tool_result("request_access", {**granted, "lease_seconds": 180}).endswith("in 3 minutes.")
    assert summarize_tool_result("request_access", {**granted, "lease_seconds": 45}).endswith("in 45 seconds.")


def test_incident_messages_use_a_short_reference_and_count_reporters_correctly() -> None:
    from agent.summary import incident_message
    from common.models import IncidentResult

    incident_id = "8dd1c866-210d-493a-a86d-ffe6afed6686"
    assert incident_message(IncidentResult(incident_id, "VPN is down", created=True, reporter_count=1)) == (
        "I opened incident INC-8dd1c866 for this. The on-call team will follow up."
    )
    assert incident_message(IncidentResult(incident_id, "VPN is down", created=False, reporter_count=1)) == (
        "You already reported this in incident INC-8dd1c866 (VPN is down). Updates will follow there."
    )
    assert incident_message(IncidentResult(incident_id, "VPN is down", created=False, reporter_count=2)) == (
        "This matches incident INC-8dd1c866 (VPN is down), which 1 other person has reported. "
        "Updates will follow there."
    )
    assert incident_message(IncidentResult(incident_id, "VPN is down", created=False, reporter_count=7)).startswith(
        "This matches incident INC-8dd1c866 (VPN is down), which 6 other people have reported."
    )
